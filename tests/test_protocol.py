"""
Tests for the user-scope systemctl seam. subprocess.run is always mocked, so these
tests run on machines without systemd.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest import mock

import pytest

from django_systemd.protocol import (
    CommandResult,
    SubprocessSystemdCtl,
    SystemdCtl,
    user_unit_dir,
)


def completed(returncode: int = 0, stdout: str = "", stderr: str = "") -> mock.Mock:
    return mock.Mock(returncode=returncode, stdout=stdout, stderr=stderr)


class TestUserUnitDir:
    def test_default_is_under_home_config(self, monkeypatch):
        monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
        assert user_unit_dir() == Path.home() / ".config" / "systemd" / "user"

    def test_honours_xdg_config_home(self, monkeypatch, tmp_path):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        assert user_unit_dir() == tmp_path / "systemd" / "user"


class TestCommandResult:
    def test_frozen(self):
        result = CommandResult(argv=("systemctl",), returncode=0, stdout="", stderr="")
        with pytest.raises(AttributeError):
            result.returncode = 1  # type: ignore[misc]


class TestSubprocessSystemdCtl:
    def _ctl(self, tmp_path: Path) -> SubprocessSystemdCtl:
        return SubprocessSystemdCtl(unit_dir=tmp_path / "units")

    def test_satisfies_protocol(self, tmp_path):
        assert isinstance(self._ctl(tmp_path), SystemdCtl)

    def test_default_unit_dir(self, monkeypatch, tmp_path):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        assert SubprocessSystemdCtl().unit_dir == tmp_path / "systemd" / "user"

    @mock.patch("django_systemd.protocol.shutil.which", return_value="/bin/systemctl")
    def test_available(self, _which, tmp_path):
        assert self._ctl(tmp_path).available is True

    @mock.patch("django_systemd.protocol.shutil.which", return_value=None)
    def test_unavailable(self, _which, tmp_path):
        assert self._ctl(tmp_path).available is False

    @mock.patch("django_systemd.protocol.subprocess.run")
    def test_always_user_scope(self, run, tmp_path):
        run.return_value = completed()
        self._ctl(tmp_path).daemon_reload()
        run.assert_called_once()
        assert run.call_args[0][0] == ["systemctl", "--user", "daemon-reload"]
        assert run.call_args[1]["check"] is False

    @mock.patch("django_systemd.protocol.subprocess.run")
    def test_nonzero_raises(self, run, tmp_path):
        run.return_value = completed(1, "", "boom")
        with pytest.raises(subprocess.CalledProcessError) as exc:
            self._ctl(tmp_path).restart("web.service")
        assert exc.value.stderr == "boom"

    @pytest.mark.parametrize(
        "method,verb,args,expected_units",
        [
            (
                "restart",
                "restart",
                ("web.service", "check.timer"),
                ["web.service", "check.timer"],
            ),
            (
                "reload",
                "reload",
                ("web.service", "check.timer"),
                ["web.service", "check.timer"],
            ),
            ("stop", "stop", ("web.service",), ["web.service"]),
            ("enable", "enable", ("web.service",), ["web.service"]),
            ("disable", "disable", ("web.service",), ["web.service"]),
        ],
    )
    @mock.patch("django_systemd.protocol.subprocess.run")
    def test_unit_verbs(self, run, method, verb, args, expected_units, tmp_path):
        run.return_value = completed()
        getattr(self._ctl(tmp_path), method)(*args)
        assert run.call_args[0][0] == ["systemctl", "--user", verb, *expected_units]

    @pytest.mark.parametrize(
        "stdout,expected",
        [
            ("active\n", True),
            ("activating\n", True),
            ("inactive\n", False),
            ("failed\n", False),
        ],
    )
    @mock.patch("django_systemd.protocol.subprocess.run")
    def test_is_active(self, run, stdout, expected, tmp_path):
        run.return_value = completed(0 if expected else 3, stdout)
        assert self._ctl(tmp_path).is_active("web.service") is expected
        assert run.call_args[0][0] == [
            "systemctl",
            "--user",
            "is-active",
            "web.service",
        ]

    @pytest.mark.parametrize(
        "stdout,expected",
        [
            ("enabled\n", True),
            ("static\n", True),
            ("indirect\n", True),
            ("disabled\n", False),
            ("masked\n", False),
        ],
    )
    @mock.patch("django_systemd.protocol.subprocess.run")
    def test_is_enabled(self, run, stdout, expected, tmp_path):
        run.return_value = completed(0 if expected else 1, stdout)
        assert self._ctl(tmp_path).is_enabled("web.service") is expected

    @pytest.mark.parametrize(
        "stdout,expected", [("yes\n", True), ("no\n", False), ("", False)]
    )
    @mock.patch("django_systemd.protocol.subprocess.run")
    def test_can_reload(self, run, stdout, expected, tmp_path):
        run.return_value = completed(0, stdout)
        assert self._ctl(tmp_path).can_reload("web.service") is expected
        assert run.call_args[0][0] == [
            "systemctl",
            "--user",
            "show",
            "--property=CanReload",
            "--value",
            "web.service",
        ]

    def test_install_unit_copies_with_mode(self, tmp_path):
        source = tmp_path / "web.service"
        source.write_text("[Unit]\nDescription=x\n")
        ctl = self._ctl(tmp_path)
        dest = ctl.install_unit(source)
        assert dest == ctl.unit_dir / "web.service"
        assert dest.read_text() == source.read_text()
        assert dest.stat().st_mode & 0o777 == 0o644
        assert ctl.is_installed("web.service") is True

    def test_install_unit_custom_name_and_overwrite(self, tmp_path):
        source = tmp_path / "web.service"
        source.write_text("v1")
        ctl = self._ctl(tmp_path)
        ctl.install_unit(source, name="renamed.service")
        source.write_text("v2")
        dest = ctl.install_unit(source, name="renamed.service")
        assert dest.name == "renamed.service"
        assert dest.read_text() == "v2"

    def test_uninstall_unit(self, tmp_path):
        source = tmp_path / "web.service"
        source.write_text("x")
        ctl = self._ctl(tmp_path)
        ctl.install_unit(source)
        assert ctl.uninstall_unit("web.service") is True
        assert ctl.is_installed("web.service") is False
        assert ctl.uninstall_unit("web.service") is False

    @pytest.mark.parametrize("name", ["", "../escaped.service", "sub/web.service"])
    def test_names_are_confined_to_unit_dir(self, name, tmp_path):
        ctl = self._ctl(tmp_path)
        source = tmp_path / "web.service"
        source.write_text("x")
        with pytest.raises(ValueError):
            ctl.install_unit(source, name=name)
        with pytest.raises(ValueError):
            ctl.uninstall_unit(name)
        with pytest.raises(ValueError):
            ctl.is_installed(name)

    def test_install_replaces_symlink_instead_of_writing_through(self, tmp_path):
        ctl = self._ctl(tmp_path)
        ctl.unit_dir.mkdir(parents=True)
        target = tmp_path / "elsewhere.service"
        target.write_text("original")
        (ctl.unit_dir / "web.service").symlink_to(target)
        source = tmp_path / "web.service"
        source.write_text("new")
        dest = ctl.install_unit(source)
        assert not dest.is_symlink()
        assert dest.read_text() == "new"
        assert target.read_text() == "original"
        assert not (ctl.unit_dir / "web.service.tmp").exists()

    def test_install_from_destination_itself(self, tmp_path):
        ctl = self._ctl(tmp_path)
        source = tmp_path / "web.service"
        source.write_text("same")
        dest = ctl.install_unit(source)
        assert ctl.install_unit(dest) == dest
        assert dest.read_text() == "same"

    def test_uninstall_removes_dangling_symlink(self, tmp_path):
        ctl = self._ctl(tmp_path)
        ctl.unit_dir.mkdir(parents=True)
        (ctl.unit_dir / "web.service").symlink_to(tmp_path / "missing.service")
        assert ctl.is_installed("web.service") is False
        assert ctl.uninstall_unit("web.service") is True
        assert not (ctl.unit_dir / "web.service").is_symlink()

    @mock.patch("django_systemd.protocol.subprocess.run", side_effect=FileNotFoundError)
    def test_missing_systemctl_raises_file_not_found(self, run, tmp_path):
        with pytest.raises(FileNotFoundError):
            self._ctl(tmp_path).daemon_reload()
