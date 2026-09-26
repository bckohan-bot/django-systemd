"""
A thin, mockable seam over ``systemctl --user`` and the user unit directory.

django-systemd assumes every unit it manages runs as the deploying user, never as
root. There is no system scope and no privilege escalation. Anything that needs
root belongs in your provisioning tooling, not here.
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
        """True if systemctl can be invoked on this machine."""
        ...

    def daemon_reload(self) -> None: ...
    def restart(self, unit: str) -> None: ...
    def reload(self, unit: str) -> None: ...
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

    def restart(self, unit: str) -> None:
        self._systemctl("restart", unit)

    def reload(self, unit: str) -> None:
        self._systemctl("reload", unit)

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
        return (
            self._systemctl("is-active", unit, check=False).stdout.strip() == "active"
        )

    def is_enabled(self, unit: str) -> bool:
        return (
            self._systemctl("is-enabled", unit, check=False).stdout.strip() == "enabled"
        )

    def is_installed(self, name: str) -> bool:
        return (self.unit_dir / name).is_file()

    def install_unit(
        self, source: Path, *, name: str | None = None, mode: int = 0o644
    ) -> Path:
        self.unit_dir.mkdir(parents=True, exist_ok=True)
        destination = self.unit_dir / (name or source.name)
        shutil.copyfile(source, destination)
        destination.chmod(mode)
        return destination

    def uninstall_unit(self, name: str) -> bool:
        destination = self.unit_dir / name
        if destination.is_file():
            destination.unlink()
            return True
        return False
