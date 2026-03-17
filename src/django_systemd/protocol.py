from __future__ import annotations

import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import (
    Iterable,
    Literal,
    Mapping,
    Protocol,
    Sequence,
    cast,
    runtime_checkable,
)

from .defines import SystemdScope

UnitState = Literal[
    "active", "inactive", "failed", "activating", "deactivating", "unknown"
]


@dataclass(frozen=True, slots=True)
class CommandResult:
    argv: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str


@dataclass(frozen=True, slots=True)
class UnitStatus:
    unit: str
    scope: SystemdScope
    is_active: bool
    state: UnitState
    # Raw `systemctl status` output is handy for logs/UI/error messages
    raw: str


@dataclass(frozen=True, slots=True)
class InstalledUnit:
    """
    Represents the effective destination on disk and whether we had to
    perform a daemon-reload or enable to activate the install.
    """

    unit_name: str
    scope: SystemdScope
    destination: Path
    daemon_reloaded: bool
    enabled: bool


@runtime_checkable
class SystemdCtl(Protocol):
    """
    A structural interface (Protocol) for a systemctl + unit installer wrapper.
    """

    # --- core systemctl lifecycle
    def daemon_reload(self, *, scope: SystemdScope = SystemdScope.SYSTEM) -> None: ...
    def start(
        self, unit: str, *, scope: SystemdScope = SystemdScope.SYSTEM
    ) -> None: ...
    def stop(self, unit: str, *, scope: SystemdScope = SystemdScope.SYSTEM) -> None: ...
    def restart(
        self, unit: str, *, scope: SystemdScope = SystemdScope.SYSTEM
    ) -> None: ...
    def reload(
        self, unit: str, *, scope: SystemdScope = SystemdScope.SYSTEM
    ) -> None: ...
    def enable(
        self, unit: str, *, scope: SystemdScope = SystemdScope.SYSTEM
    ) -> None: ...
    def disable(
        self, unit: str, *, scope: SystemdScope = SystemdScope.SYSTEM
    ) -> None: ...
    def mask(self, unit: str, *, scope: SystemdScope = SystemdScope.SYSTEM) -> None: ...
    def unmask(
        self, unit: str, *, scope: SystemdScope = SystemdScope.SYSTEM
    ) -> None: ...

    # --- querying
    def is_active(
        self, unit: str, *, scope: SystemdScope = SystemdScope.SYSTEM
    ) -> bool: ...
    def is_enabled(
        self, unit: str, *, scope: SystemdScope = SystemdScope.SYSTEM
    ) -> bool: ...
    def status(
        self, unit: str, *, scope: SystemdScope = SystemdScope.SYSTEM
    ) -> UnitStatus: ...

    def list_units(
        self,
        *,
        scope: SystemdScope = SystemdScope.SYSTEM,
        states: Iterable[UnitState] = ("active", "inactive", "failed"),
    ) -> Sequence[str]:
        """
        Return unit names. Typically maps to:
          systemctl list-units --no-legend --plain --state=...
        """
        ...

    def list_unit_files(
        self, *, scope: SystemdScope = SystemdScope.SYSTEM
    ) -> Mapping[str, str]:
        """
        Return mapping unit_name -> enabled_state.
        Typically maps to:
          systemctl list-unit-files --no-legend --plain
        """
        ...

    # --- install units
    def install_unit(
        self,
        unit_source: Path,
        *,
        scope: SystemdScope = SystemdScope.SYSTEM,
        name: str | None = None,
        enable: bool = False,
        daemon_reload: bool = True,
        mode: int = 0o644,
    ) -> InstalledUnit:
        """
        "Install" a unit file onto disk in an appropriate unit search path.

        - scope="system": typically /etc/systemd/system
        - scope="user": typically ~/.config/systemd/user

        Implementations should:
          - create destination dirs
          - copy bytes from unit_source to destination (atomic replace preferred)
          - chmod to `mode`
          - optionally run daemon-reload
          - optionally enable the unit (creates symlinks under wants/)

        Note: This is intentionally NOT `systemctl link` vs `install` vs "drop-ins";
              it's a pragmatic "copy into unit dir" operation.
        """
        ...

    def uninstall_unit(
        self,
        unit_name: str,
        *,
        scope: SystemdScope = SystemdScope.SYSTEM,
        disable: bool = True,
        daemon_reload: bool = True,
    ) -> None:
        """
        Remove installed unit file (and optionally disable it), then reload.
        """


class SubprocessSystemdCtl:
    """
    Implementation of SystemdCtl using subprocess to call systemctl.

    If a command fails due to permissions, it retries with sudo.
    """

    PERMISSION_ERRORS = (
        "Access denied",
        "Permission denied",
        "Interactive authentication required",
        "authentication required",
        "polkit",
    )

    def _run(
        self,
        args: Sequence[str],
        *,
        scope: SystemdScope,
        check: bool = True,
        use_sudo: bool = False,
    ) -> CommandResult:
        """
        Run a command, retrying with sudo on permission errors.
        """
        cmd = ["sudo", *args] if use_sudo else list(args)
        result = subprocess.run(cmd, capture_output=True, text=True)
        cmd_result = CommandResult(
            argv=tuple(cmd),
            returncode=result.returncode,
            stdout=result.stdout,
            stderr=result.stderr,
        )

        # Check for permission errors and retry with sudo if needed
        if (
            not use_sudo
            and result.returncode != 0
            and scope == SystemdScope.SYSTEM
            and any(err in result.stderr for err in self.PERMISSION_ERRORS)
        ):
            return self._run(args, scope=scope, check=check, use_sudo=True)

        if check and result.returncode != 0:
            raise subprocess.CalledProcessError(
                result.returncode, cmd, result.stdout, result.stderr
            )

        return cmd_result

    def _systemctl(
        self,
        *args: str,
        scope: SystemdScope,
        check: bool = True,
    ) -> CommandResult:
        """
        Run systemctl with the appropriate scope flag.
        """
        scope_flag = "--user" if scope == SystemdScope.USER else "--system"
        return self._run(
            ["systemctl", scope_flag, *args],
            scope=scope,
            check=check,
        )

    def daemon_reload(self, *, scope: SystemdScope = SystemdScope.SYSTEM) -> None:
        self._systemctl("daemon-reload", scope=scope)

    def start(self, unit: str, *, scope: SystemdScope = SystemdScope.SYSTEM) -> None:
        self._systemctl("start", unit, scope=scope)

    def stop(self, unit: str, *, scope: SystemdScope = SystemdScope.SYSTEM) -> None:
        self._systemctl("stop", unit, scope=scope)

    def restart(self, unit: str, *, scope: SystemdScope = SystemdScope.SYSTEM) -> None:
        self._systemctl("restart", unit, scope=scope)

    def reload(self, unit: str, *, scope: SystemdScope = SystemdScope.SYSTEM) -> None:
        self._systemctl("reload", unit, scope=scope)

    def enable(self, unit: str, *, scope: SystemdScope = SystemdScope.SYSTEM) -> None:
        self._systemctl("enable", unit, scope=scope)

    def disable(self, unit: str, *, scope: SystemdScope = SystemdScope.SYSTEM) -> None:
        self._systemctl("disable", unit, scope=scope)

    def mask(self, unit: str, *, scope: SystemdScope = SystemdScope.SYSTEM) -> None:
        self._systemctl("mask", unit, scope=scope)

    def unmask(self, unit: str, *, scope: SystemdScope = SystemdScope.SYSTEM) -> None:
        self._systemctl("unmask", unit, scope=scope)

    def is_active(
        self, unit: str, *, scope: SystemdScope = SystemdScope.SYSTEM
    ) -> bool:
        result = self._systemctl("is-active", unit, scope=scope, check=False)
        return result.stdout.strip() == "active"

    def is_enabled(
        self, unit: str, *, scope: SystemdScope = SystemdScope.SYSTEM
    ) -> bool:
        result = self._systemctl("is-enabled", unit, scope=scope, check=False)
        return result.stdout.strip() == "enabled"

    def status(
        self, unit: str, *, scope: SystemdScope = SystemdScope.SYSTEM
    ) -> UnitStatus:
        result = self._systemctl("status", unit, scope=scope, check=False)
        is_active_result = self._systemctl("is-active", unit, scope=scope, check=False)
        state_str = is_active_result.stdout.strip()

        # Map the state string to a valid UnitState
        valid_states: set[UnitState] = {
            "active",
            "inactive",
            "failed",
            "activating",
            "deactivating",
            "unknown",
        }
        state: UnitState = (
            cast(UnitState, state_str) if state_str in valid_states else "unknown"
        )

        return UnitStatus(
            unit=unit,
            scope=scope,
            is_active=(state == "active"),
            state=state,
            raw=result.stdout,
        )

    def list_units(
        self,
        *,
        scope: SystemdScope = SystemdScope.SYSTEM,
        states: Iterable[UnitState] = ("active", "inactive", "failed"),
    ) -> Sequence[str]:
        state_arg = ",".join(states)
        result = self._systemctl(
            "list-units",
            "--no-legend",
            "--plain",
            f"--state={state_arg}",
            scope=scope,
        )
        units = []
        for line in result.stdout.strip().splitlines():
            if line:
                # First column is the unit name
                parts = line.split()
                if parts:
                    units.append(parts[0])
        return units

    def list_unit_files(
        self, *, scope: SystemdScope = SystemdScope.SYSTEM
    ) -> Mapping[str, str]:
        result = self._systemctl(
            "list-unit-files", "--no-legend", "--plain", scope=scope
        )
        unit_files: dict[str, str] = {}
        for line in result.stdout.strip().splitlines():
            if line:
                parts = line.split()
                if len(parts) >= 2:
                    unit_files[parts[0]] = parts[1]
        return unit_files

    def install_unit(
        self,
        unit_source: Path,
        *,
        scope: SystemdScope = SystemdScope.SYSTEM,
        name: str | None = None,
        enable: bool = False,
        daemon_reload: bool = True,
        mode: int = 0o644,
    ) -> InstalledUnit:
        unit_name = name or unit_source.name
        dest_dir = scope.location[0].expanduser()
        destination = dest_dir / unit_name

        if scope == SystemdScope.USER:
            # User scope: direct file operations
            dest_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(unit_source, destination)
            destination.chmod(mode)
        else:
            # System scope: may need sudo for file operations
            try:
                dest_dir.mkdir(parents=True, exist_ok=True)
                shutil.copy2(unit_source, destination)
                destination.chmod(mode)
            except PermissionError:
                # Use sudo to copy the file
                self._run(["mkdir", "-p", str(dest_dir)], scope=scope, use_sudo=True)
                # Copy to temp file first, then sudo mv to destination
                with tempfile.NamedTemporaryFile(delete=False) as tmp:
                    tmp_path = Path(tmp.name)
                    shutil.copy2(unit_source, tmp_path)
                    tmp_path.chmod(mode)
                self._run(
                    ["cp", str(tmp_path), str(destination)], scope=scope, use_sudo=True
                )
                self._run(
                    ["chmod", oct(mode)[2:], str(destination)],
                    scope=scope,
                    use_sudo=True,
                )
                tmp_path.unlink()

        reloaded = False
        if daemon_reload:
            self.daemon_reload(scope=scope)
            reloaded = True

        enabled = False
        if enable:
            self.enable(unit_name, scope=scope)
            enabled = True

        return InstalledUnit(
            unit_name=unit_name,
            scope=scope,
            destination=destination,
            daemon_reloaded=reloaded,
            enabled=enabled,
        )

    def uninstall_unit(
        self,
        unit_name: str,
        *,
        scope: SystemdScope = SystemdScope.SYSTEM,
        disable: bool = True,
        daemon_reload: bool = True,
    ) -> None:
        if disable:
            # Ignore errors if unit is not enabled
            try:
                self.disable(unit_name, scope=scope)
            except subprocess.CalledProcessError:
                pass

        dest_dir = scope.location[0].expanduser()
        destination = dest_dir / unit_name

        if destination.exists():
            if scope == SystemdScope.USER:
                destination.unlink()
            else:
                try:
                    destination.unlink()
                except PermissionError:
                    self._run(["rm", str(destination)], scope=scope, use_sudo=True)

        if daemon_reload:
            self.daemon_reload(scope=scope)
