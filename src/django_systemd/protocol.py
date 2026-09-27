"""
A thin, mockable seam over ``systemctl --user`` and the user unit directory.

django-systemd assumes every unit it manages runs as the deploying user, never as
root. There is no system scope and no privilege escalation. Anything that needs
root belongs in your provisioning tooling, not here.

Talking to the user manager from a non-login session (for example over SSH as a
deploy user) requires lingering to be enabled for that user with
``loginctl enable-linger``, or ``XDG_RUNTIME_DIR`` to be set. Failures show up as
``Failed to connect to bus`` in the raised CalledProcessError.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable


def user_unit_dir() -> Path:
    """
    The directory systemd searches for user units that we install into.

    This is ``$XDG_CONFIG_HOME/systemd/user`` when ``XDG_CONFIG_HOME`` is set,
    otherwise ``~/.config/systemd/user``.
    """
    base = os.environ.get("XDG_CONFIG_HOME")
    root = Path(base) if base else Path.home() / ".config"
    return root / "systemd" / "user"


@dataclass(frozen=True, slots=True)
class CommandResult:
    """The outcome of one systemctl invocation."""

    argv: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str


@runtime_checkable
class SystemdCtl(Protocol):
    """
    What the :class:`systemd command <django_systemd.management.commands.systemd.Command>`
    needs from systemd. Implement this to swap in a fake for tests or a different
    transport.
    """

    unit_dir: Path

    @property
    def available(self) -> bool:
        """
        True if a systemctl binary is on PATH. This does not check that the user
        manager is reachable; callers must check it before calling any other
        systemctl-backed method, which raise FileNotFoundError when systemctl is
        absent. ``is_installed``, ``install_unit`` and ``uninstall_unit`` are
        filesystem-only and do not require this check.
        """
        ...

    def daemon_reload(self) -> None: ...
    def restart(self, *units: str) -> None: ...
    def reload(self, *units: str) -> None: ...
    def stop(self, unit: str) -> None: ...
    def can_reload(self, unit: str) -> bool:
        """True if the unit defines a reload action (``ExecReload=``)."""
        ...

    def enable(self, unit: str) -> None: ...
    def disable(self, unit: str) -> None: ...
    def is_active(self, unit: str) -> bool: ...
    def is_enabled(self, unit: str) -> bool: ...

    def is_installed(self, name: str) -> bool:
        """True if a unit file with this name exists in :attr:`unit_dir`."""
        ...

    def install_unit(
        self, source: Path, *, name: str | None = None, mode: int = 0o644
    ) -> Path:
        """
        Copy ``source`` into :attr:`unit_dir`, replacing any existing file.

        :param source: The rendered unit file to install.
        :param name: Install under this file name instead of ``source.name``.
        :param mode: File mode to apply to the installed unit.
        :return: The path of the installed unit file.
        """
        ...

    def uninstall_unit(self, name: str) -> bool:
        """
        Remove the unit file with this name from :attr:`unit_dir`.

        :return: True if a file was removed, False if there was nothing to remove.
        """
        ...


class SubprocessSystemdCtl:
    """
    :class:`SystemdCtl` implemented by shelling out to ``systemctl --user``.

    :param unit_dir: Where to install unit files. Defaults to :func:`user_unit_dir`.
    """

    # systemctl is-enabled prints one of many states; these all mean "will start".
    _ENABLED_STATES = frozenset(
        {"enabled", "enabled-runtime", "static", "indirect", "alias"}
    )
    # is-active states that mean the unit is up or coming up.
    _ACTIVE_STATES = frozenset({"active", "activating", "reloading"})

    def __init__(self, unit_dir: Path | None = None) -> None:
        self.unit_dir = unit_dir or user_unit_dir()

    @property
    def available(self) -> bool:
        return shutil.which("systemctl") is not None

    def _systemctl(self, *args: str, check: bool = True) -> CommandResult:
        cmd = ["systemctl", "--user", *args]
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if check and result.returncode != 0:
            raise subprocess.CalledProcessError(
                result.returncode, cmd, result.stdout, result.stderr
            )
        return CommandResult(
            argv=tuple(cmd),
            returncode=result.returncode,
            stdout=result.stdout,
            stderr=result.stderr,
        )

    def daemon_reload(self) -> None:
        self._systemctl("daemon-reload")

    def restart(self, *units: str) -> None:
        self._systemctl("restart", *units)

    def reload(self, *units: str) -> None:
        self._systemctl("reload", *units)

    def stop(self, unit: str) -> None:
        self._systemctl("stop", unit)

    def can_reload(self, unit: str) -> bool:
        result = self._systemctl(
            "show", "--property=CanReload", "--value", unit, check=False
        )
        return result.stdout.strip() == "yes"

    def enable(self, unit: str) -> None:
        self._systemctl("enable", unit)

    def disable(self, unit: str) -> None:
        self._systemctl("disable", unit)

    def is_active(self, unit: str) -> bool:
        result = self._systemctl("is-active", unit, check=False)
        if result.returncode != 0 and not result.stdout:
            raise subprocess.CalledProcessError(
                result.returncode, result.argv, result.stdout, result.stderr
            )
        return result.stdout.strip() in self._ACTIVE_STATES

    def is_enabled(self, unit: str) -> bool:
        result = self._systemctl("is-enabled", unit, check=False)
        if result.returncode != 0 and not result.stdout:
            raise subprocess.CalledProcessError(
                result.returncode, result.argv, result.stdout, result.stderr
            )
        return result.stdout.strip() in self._ENABLED_STATES

    def _unit_path(self, name: str) -> Path:
        if not name or Path(name).name != name:
            raise ValueError(f"Not a bare unit file name: {name!r}")
        return self.unit_dir / name

    def is_installed(self, name: str) -> bool:
        return self._unit_path(name).is_file()

    def install_unit(
        self, source: Path, *, name: str | None = None, mode: int = 0o644
    ) -> Path:
        destination = self._unit_path(name if name is not None else source.name)
        self.unit_dir.mkdir(parents=True, exist_ok=True)
        staged = destination.with_name(destination.name + ".tmp")
        try:
            shutil.copyfile(source, staged)
            staged.chmod(mode)
            os.replace(staged, destination)
        except OSError:
            staged.unlink(missing_ok=True)
            raise
        return destination

    def uninstall_unit(self, name: str) -> bool:
        destination = self._unit_path(name)
        if destination.is_file() or destination.is_symlink():
            destination.unlink()
            return True
        return False
