"""
Tests for django-systemd: config, defines, protocol, and management commands.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Sequence
from unittest import mock

import pytest
from django.core.management import call_command
from django.template.exceptions import TemplateDoesNotExist
from django.test import TestCase, override_settings

from django_systemd.config import (
    SERVICE_UNIT_REGEX,
    ServiceUnit,
    render_engine,
    service_units,
    template_engine_config,
)
from django_systemd.defines import (
    SystemdRestartType,
    SystemdScope,
    SystemdStartupType,
    SystemdUnitType,
)
from django_systemd.protocol import (
    CommandResult,
    InstalledUnit,
    SubprocessSystemdCtl,
    SystemdCtl,
    UnitStatus,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def clear_lru_caches():
    """Clear module-level lru_cache between tests for isolation."""
    render_engine.cache_clear()
    template_engine_config.cache_clear()
    yield
    render_engine.cache_clear()
    template_engine_config.cache_clear()


# ---------------------------------------------------------------------------
# defines.py
# ---------------------------------------------------------------------------


class TestSystemdUnitType:
    def test_all_values(self):
        values = {u.value for u in SystemdUnitType}
        assert "service" in values
        assert "socket" in values
        assert "target" in values
        assert "timer" in values
        assert "path" in values
        assert "mount" in values
        assert "automount" in values
        assert "swap" in values
        assert "device" in values
        assert "scope" in values
        assert "snapshot" in values
        assert "slice" in values

    def test_str(self):
        assert str(SystemdUnitType.SERVICE) == "service"
        assert str(SystemdUnitType.TIMER) == "timer"

    def test_description(self):
        assert "daemon" in SystemdUnitType.SERVICE.description.lower()
        assert "timer" in SystemdUnitType.TIMER.description.lower()

    def test_count(self):
        assert len(list(SystemdUnitType)) == 12


class TestSystemdStartupType:
    def test_all_values(self):
        values = {s.value for s in SystemdStartupType}
        assert "simple" in values
        assert "exec" in values
        assert "forking" in values
        assert "oneshot" in values
        assert "dbus" in values
        assert "notify" in values
        assert "notify-reload" in values
        assert "idle" in values

    def test_str(self):
        assert str(SystemdStartupType.SIMPLE) == "simple"
        assert str(SystemdStartupType.NOTIFY_RELOAD) == "notify-reload"

    def test_description(self):
        assert SystemdStartupType.FORKING.description


class TestSystemdRestartType:
    def test_all_values(self):
        values = {r.value for r in SystemdRestartType}
        assert "no" in values
        assert "on-success" in values
        assert "on-failure" in values
        assert "on-abnormal" in values
        assert "on-watchdog" in values
        assert "on-abort" in values
        assert "always" in values

    def test_str(self):
        assert str(SystemdRestartType.ON_FAILURE) == "on-failure"
        assert str(SystemdRestartType.ALWAYS) == "always"

    def test_description(self):
        assert SystemdRestartType.ALWAYS.description


class TestSystemdScope:
    def test_values(self):
        assert str(SystemdScope.USER) == "user"
        assert str(SystemdScope.SYSTEM) == "system"

    def test_location(self):
        assert isinstance(SystemdScope.USER.location, list)
        assert len(SystemdScope.USER.location) > 0
        assert "~/.config/systemd/user" in str(SystemdScope.USER.location[0])
        assert "/etc/systemd/system" in str(SystemdScope.SYSTEM.location[0])

    def test_description(self):
        assert "user" in SystemdScope.USER.description.lower()
        assert "system" in SystemdScope.SYSTEM.description.lower()


# ---------------------------------------------------------------------------
# config.py
# ---------------------------------------------------------------------------


class TestServiceUnitRegex:
    def test_service(self):
        m = SERVICE_UNIT_REGEX.match("web.service")
        assert m is not None
        assert m.group("name") == "web"
        assert m.group("type") == "service"

    def test_timer(self):
        m = SERVICE_UNIT_REGEX.match("check.timer")
        assert m is not None
        assert m.group("type") == "timer"

    def test_instanceable(self):
        m = SERVICE_UNIT_REGEX.match("app@.target")
        assert m is not None
        assert "@" in m.group("name")

    def test_no_match(self):
        assert SERVICE_UNIT_REGEX.match("bad.xyz") is None
        assert SERVICE_UNIT_REGEX.match("no_extension") is None


@pytest.mark.django_db
class TestServiceUnit:
    def test_parse_string(self):
        unit = ServiceUnit.parse("web.service")
        assert unit.name == "web"
        assert unit.unit_type == SystemdUnitType.SERVICE
        assert not unit.instanceable
        assert unit.path is None

    def test_parse_path(self):
        unit = ServiceUnit.parse(Path("check.timer"))
        assert unit.name == "check"
        assert unit.unit_type == SystemdUnitType.TIMER

    def test_parse_instanceable(self):
        unit = ServiceUnit.parse("app@.target")
        assert unit.instanceable is True
        assert unit.unit_type == SystemdUnitType.TARGET

    def test_parse_invalid_raises(self):
        with pytest.raises(ValueError, match="Unrecognized unit name"):
            ServiceUnit.parse("bad.xyz")

    def test_parse_invalid_no_ext_raises(self):
        with pytest.raises(ValueError, match="Unrecognized unit name"):
            ServiceUnit.parse("noextension")


@pytest.mark.django_db
class TestServiceUnits:
    def test_returns_all_unit_types(self):
        units = service_units()
        for unit_type in SystemdUnitType:
            assert unit_type.value in units

    def test_returns_service_unit_instances(self):
        units = service_units()
        assert isinstance(units["service"], ServiceUnit)

    def test_cached(self):
        assert service_units() is service_units()


@pytest.mark.django_db
class TestTemplateEngineConfig:
    def test_has_required_keys(self):
        cfg = template_engine_config()
        assert "ENGINES" in cfg
        assert "context" in cfg
        assert "templates" in cfg

    def test_context_has_standard_vars(self):
        cfg = template_engine_config()
        ctx = cfg["context"]
        assert "settings" in ctx
        assert "venv" in ctx
        assert "python" in ctx
        assert "django-admin" in ctx
        assert "DJANGO_SETTINGS_MODULE" in ctx

    def test_context_venv_is_path(self):
        cfg = template_engine_config()
        assert isinstance(cfg["context"]["venv"], Path)

    def test_context_python_is_path(self):
        cfg = template_engine_config()
        assert isinstance(cfg["context"]["python"], Path)

    def test_templates_patterns(self):
        cfg = template_engine_config()
        assert any("service" in p for p in cfg["templates"])
        assert any("timer" in p for p in cfg["templates"])

    def test_custom_context_from_settings(self):
        with override_settings(SYSTEMD_TEMPLATE_CONTEXT={"MY_VAR": "hello"}):
            template_engine_config.cache_clear()
            cfg = template_engine_config()
            assert cfg["context"].get("MY_VAR") == "hello"

    def test_custom_templates_from_settings(self):
        with override_settings(SYSTEMD_TEMPLATES=["**/*.service"]):
            template_engine_config.cache_clear()
            cfg = template_engine_config()
            assert cfg["templates"] == ["**/*.service"]

    def test_custom_engine_from_settings(self):
        custom_engine = {
            "ENGINES": [
                {
                    "BACKEND": "render_static.backends.StaticDjangoTemplates",
                    "OPTIONS": {
                        "app_dir": "systemd",
                        "loaders": [
                            "render_static.loaders.StaticAppDirectoriesBatchLoader"
                        ],
                        "builtins": ["render_static.templatetags.render_static"],
                    },
                }
            ],
        }
        with override_settings(SYSTEMD_TEMPLATE_ENGINE=custom_engine):
            template_engine_config.cache_clear()
            cfg = template_engine_config()
            assert cfg["ENGINES"] == custom_engine["ENGINES"]


@pytest.mark.django_db
class TestRenderEngine:
    def test_returns_engine(self):
        from render_static.engine import StaticTemplateEngine

        engine = render_engine()
        assert isinstance(engine, StaticTemplateEngine)

    def test_cached(self):
        assert render_engine() is render_engine()

    def test_discovers_templates(self):
        engine = render_engine()
        names = {tmpl.name for tmpl in engine.search("")}
        assert "web.service" in names
        assert "check.timer" in names
        assert "app@.target" in names

    def test_app_precedence(self):
        """app2 (higher in INSTALLED_APPS) should take precedence over app1."""
        engine = render_engine()
        # Collect the first occurrence of each template name (highest precedence)
        first_seen: dict[str, str] = {}
        for tmpl in engine.search(""):
            if tmpl.name not in first_seen:
                first_seen[tmpl.name] = str(tmpl.origin)

        # app2 is listed before app1 in INSTALLED_APPS, so it wins
        assert "app2" in first_seen["web.service"]
        assert "app2" in first_seen["check.timer"]
        assert "app2" in first_seen["app@.target"]

    def test_render_service_template(self):
        """Rendered service file should contain rendered context variables."""
        engine = render_engine()
        with tempfile.TemporaryDirectory() as tmp:
            renders = list(engine.render_each("**/*.service", dest=tmp))
            assert len(renders) == 1
            content = Path(renders[0].destination).read_text()
            # Template variable {{ python }} should be substituted
            assert str(sys.executable) in content
            # app2 override marker should appear
            assert "app2 override" in content

    def test_render_timer_template(self):
        engine = render_engine()
        with tempfile.TemporaryDirectory() as tmp:
            renders = list(engine.render_each("**/*.timer", dest=tmp))
            assert len(renders) == 1
            content = Path(renders[0].destination).read_text()
            assert "app2 override" in content

    def test_render_unknown_pattern_raises(self):
        engine = render_engine()
        with pytest.raises(TemplateDoesNotExist):
            list(engine.render_each("**/*.socket", dest="/tmp"))


# ---------------------------------------------------------------------------
# protocol.py
# ---------------------------------------------------------------------------


class TestCommandResult:
    def test_frozen(self):
        r = CommandResult(argv=("cmd",), returncode=0, stdout="out", stderr="")
        with pytest.raises((AttributeError, TypeError)):
            r.returncode = 1  # type: ignore[misc]

    def test_fields(self):
        r = CommandResult(argv=("a", "b"), returncode=1, stdout="o", stderr="e")
        assert r.argv == ("a", "b")
        assert r.returncode == 1
        assert r.stdout == "o"
        assert r.stderr == "e"


class TestUnitStatus:
    def test_frozen(self):
        s = UnitStatus(
            unit="foo.service",
            scope=SystemdScope.SYSTEM,
            is_active=True,
            state="active",
            raw="raw output",
        )
        with pytest.raises((AttributeError, TypeError)):
            s.is_active = False  # type: ignore[misc]

    def test_fields(self):
        s = UnitStatus(
            unit="bar.service",
            scope=SystemdScope.USER,
            is_active=False,
            state="inactive",
            raw="",
        )
        assert s.unit == "bar.service"
        assert s.scope == SystemdScope.USER
        assert s.state == "inactive"


class TestInstalledUnit:
    def test_frozen(self):
        u = InstalledUnit(
            unit_name="x.service",
            scope=SystemdScope.SYSTEM,
            destination=Path("/tmp/x.service"),
            daemon_reloaded=True,
            enabled=False,
        )
        with pytest.raises((AttributeError, TypeError)):
            u.enabled = True  # type: ignore[misc]


class TestSystemdCtlProtocol:
    def test_subprocess_impl_satisfies_protocol(self):
        ctl = SubprocessSystemdCtl()
        assert isinstance(ctl, SystemdCtl)


class TestSubprocessSystemdCtl:
    def _ctl(self) -> SubprocessSystemdCtl:
        return SubprocessSystemdCtl()

    def _make_run_result(self, returncode=0, stdout="", stderr=""):
        return mock.Mock(returncode=returncode, stdout=stdout, stderr=stderr)

    # --- _run ---

    @mock.patch("subprocess.run")
    def test_run_success(self, mock_run):
        mock_run.return_value = self._make_run_result(0, "ok", "")
        result = self._ctl()._run(["echo"], scope=SystemdScope.SYSTEM)
        assert result.returncode == 0
        assert result.stdout == "ok"
        assert result.argv == ("echo",)

    @mock.patch("subprocess.run")
    def test_run_sudo_retry_on_permission_error(self, mock_run):
        mock_run.side_effect = [
            self._make_run_result(1, "", "Access denied"),
            self._make_run_result(0, "ok", ""),
        ]
        result = self._ctl()._run(["systemctl", "start"], scope=SystemdScope.SYSTEM)
        assert result.returncode == 0
        # Second call should be with sudo
        assert mock_run.call_args_list[1][0][0][0] == "sudo"

    @mock.patch("subprocess.run")
    def test_run_no_sudo_retry_for_user_scope(self, mock_run):
        mock_run.return_value = self._make_run_result(1, "", "Access denied")
        with pytest.raises(subprocess.CalledProcessError):
            self._ctl()._run(["systemctl"], scope=SystemdScope.USER)
        # Should only be called once — no retry for user scope
        assert mock_run.call_count == 1

    @mock.patch("subprocess.run")
    def test_run_check_false_no_raise(self, mock_run):
        mock_run.return_value = self._make_run_result(1, "", "error")
        result = self._ctl()._run(
            ["cmd"], scope=SystemdScope.SYSTEM, check=False, use_sudo=True
        )
        assert result.returncode == 1

    @mock.patch("subprocess.run")
    def test_run_check_true_raises_on_nonzero(self, mock_run):
        mock_run.return_value = self._make_run_result(1, "", "some error")
        with pytest.raises(subprocess.CalledProcessError):
            self._ctl()._run(
                ["cmd"], scope=SystemdScope.SYSTEM, check=True, use_sudo=True
            )

    @mock.patch("subprocess.run")
    def test_run_all_permission_error_strings(self, mock_run):
        """Each permission error string should trigger a sudo retry."""
        errors = [
            "Access denied",
            "Permission denied",
            "Interactive authentication required",
            "authentication required",
            "polkit",
        ]
        for err_msg in errors:
            mock_run.reset_mock()
            mock_run.side_effect = [
                self._make_run_result(1, "", err_msg),
                self._make_run_result(0, "ok", ""),
            ]
            result = self._ctl()._run(["cmd"], scope=SystemdScope.SYSTEM)
            assert result.returncode == 0, f"Failed for error: {err_msg}"

    # --- lifecycle methods ---

    @mock.patch("subprocess.run")
    def test_daemon_reload(self, mock_run):
        mock_run.return_value = self._make_run_result()
        self._ctl().daemon_reload(scope=SystemdScope.SYSTEM)
        cmd = mock_run.call_args[0][0]
        assert "daemon-reload" in cmd

    @mock.patch("subprocess.run")
    def test_daemon_reload_user_scope(self, mock_run):
        mock_run.return_value = self._make_run_result()
        self._ctl().daemon_reload(scope=SystemdScope.USER)
        cmd = mock_run.call_args[0][0]
        assert "--user" in cmd

    @mock.patch("subprocess.run")
    def test_start(self, mock_run):
        mock_run.return_value = self._make_run_result()
        self._ctl().start("web.service", scope=SystemdScope.SYSTEM)
        cmd = mock_run.call_args[0][0]
        assert "start" in cmd and "web.service" in cmd

    @mock.patch("subprocess.run")
    def test_stop(self, mock_run):
        mock_run.return_value = self._make_run_result()
        self._ctl().stop("web.service", scope=SystemdScope.SYSTEM)
        cmd = mock_run.call_args[0][0]
        assert "stop" in cmd

    @mock.patch("subprocess.run")
    def test_restart(self, mock_run):
        mock_run.return_value = self._make_run_result()
        self._ctl().restart("web.service", scope=SystemdScope.SYSTEM)
        cmd = mock_run.call_args[0][0]
        assert "restart" in cmd

    @mock.patch("subprocess.run")
    def test_reload(self, mock_run):
        mock_run.return_value = self._make_run_result()
        self._ctl().reload("web.service", scope=SystemdScope.SYSTEM)
        cmd = mock_run.call_args[0][0]
        assert "reload" in cmd

    @mock.patch("subprocess.run")
    def test_enable(self, mock_run):
        mock_run.return_value = self._make_run_result()
        self._ctl().enable("web.service", scope=SystemdScope.SYSTEM)
        cmd = mock_run.call_args[0][0]
        assert "enable" in cmd

    @mock.patch("subprocess.run")
    def test_disable(self, mock_run):
        mock_run.return_value = self._make_run_result()
        self._ctl().disable("web.service", scope=SystemdScope.SYSTEM)
        cmd = mock_run.call_args[0][0]
        assert "disable" in cmd

    @mock.patch("subprocess.run")
    def test_mask(self, mock_run):
        mock_run.return_value = self._make_run_result()
        self._ctl().mask("web.service", scope=SystemdScope.SYSTEM)
        cmd = mock_run.call_args[0][0]
        assert "mask" in cmd

    @mock.patch("subprocess.run")
    def test_unmask(self, mock_run):
        mock_run.return_value = self._make_run_result()
        self._ctl().unmask("web.service", scope=SystemdScope.SYSTEM)
        cmd = mock_run.call_args[0][0]
        assert "unmask" in cmd

    # --- querying ---

    @mock.patch("subprocess.run")
    def test_is_active_true(self, mock_run):
        mock_run.return_value = self._make_run_result(0, "active\n", "")
        assert self._ctl().is_active("web.service") is True

    @mock.patch("subprocess.run")
    def test_is_active_false(self, mock_run):
        mock_run.return_value = self._make_run_result(3, "inactive\n", "")
        assert self._ctl().is_active("web.service") is False

    @mock.patch("subprocess.run")
    def test_is_enabled_true(self, mock_run):
        mock_run.return_value = self._make_run_result(0, "enabled\n", "")
        assert self._ctl().is_enabled("web.service") is True

    @mock.patch("subprocess.run")
    def test_is_enabled_false(self, mock_run):
        mock_run.return_value = self._make_run_result(1, "disabled\n", "")
        assert self._ctl().is_enabled("web.service") is False

    @mock.patch("subprocess.run")
    def test_status_active(self, mock_run):
        mock_run.side_effect = [
            self._make_run_result(0, "● web.service - Web\n   Active: active", ""),
            self._make_run_result(0, "active\n", ""),
        ]
        status = self._ctl().status("web.service")
        assert status.is_active is True
        assert status.state == "active"

    @mock.patch("subprocess.run")
    def test_status_inactive(self, mock_run):
        mock_run.side_effect = [
            self._make_run_result(3, "● web.service - Web\n   Active: inactive", ""),
            self._make_run_result(3, "inactive\n", ""),
        ]
        status = self._ctl().status("web.service")
        assert status.is_active is False
        assert status.state == "inactive"

    @mock.patch("subprocess.run")
    def test_status_unknown_state(self, mock_run):
        mock_run.side_effect = [
            self._make_run_result(4, "", ""),
            self._make_run_result(4, "weird-state\n", ""),
        ]
        status = self._ctl().status("web.service")
        assert status.state == "unknown"

    @mock.patch("subprocess.run")
    def test_list_units(self, mock_run):
        output = "web.service  loaded active running Web\n"
        mock_run.return_value = self._make_run_result(0, output, "")
        units = self._ctl().list_units(scope=SystemdScope.SYSTEM)
        assert "web.service" in units

    @mock.patch("subprocess.run")
    def test_list_units_empty(self, mock_run):
        mock_run.return_value = self._make_run_result(0, "\n", "")
        units = self._ctl().list_units(scope=SystemdScope.SYSTEM)
        assert units == []

    @mock.patch("subprocess.run")
    def test_list_units_empty_lines_skipped(self, mock_run):
        """Empty and whitespace-only lines in systemctl output should be skipped.

        Empty lines must be in the middle of the output to survive `strip()`.
        A whitespace-only line (truthy) with no columns exercises the `if parts:` branch.
        """
        # Empty line (\n\n) exercises `if line:` → False.
        # Whitespace-only line ("   ") exercises `if parts:` → False.
        output = "web.service  loaded active running Web\n\n   \ncheck.timer  active\n"
        mock_run.return_value = self._make_run_result(0, output, "")
        units = self._ctl().list_units(scope=SystemdScope.SYSTEM)
        assert "web.service" in units
        assert "check.timer" in units

    @mock.patch("subprocess.run")
    def test_list_unit_files(self, mock_run):
        output = "web.service  enabled\ncheck.timer  disabled\n"
        mock_run.return_value = self._make_run_result(0, output, "")
        files = self._ctl().list_unit_files(scope=SystemdScope.SYSTEM)
        assert files["web.service"] == "enabled"
        assert files["check.timer"] == "disabled"

    @mock.patch("subprocess.run")
    def test_list_unit_files_empty(self, mock_run):
        mock_run.return_value = self._make_run_result(0, "\n", "")
        files = self._ctl().list_unit_files(scope=SystemdScope.SYSTEM)
        assert files == {}

    @mock.patch("subprocess.run")
    def test_list_unit_files_empty_and_short_lines_skipped(self, mock_run):
        """Empty lines and single-column lines should be skipped.

        Lines must be in the middle of the output to survive `strip()`.
        """
        output = "web.service  enabled\n\n   \norphan-unit\ncheck.timer  disabled\n"
        mock_run.return_value = self._make_run_result(0, output, "")
        files = self._ctl().list_unit_files(scope=SystemdScope.SYSTEM)
        assert "web.service" in files
        assert "orphan-unit" not in files
        assert "check.timer" in files

    # --- install_unit ---

    def test_install_unit_user_scope(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "web.service"
            src.write_text("[Unit]\nDescription=Test\n")
            dest_dir = Path(tmp) / "user_units"
            with (
                mock.patch.object(SystemdScope.USER, "location", [dest_dir]),
                mock.patch.object(SubprocessSystemdCtl, "daemon_reload") as mock_reload,
            ):
                result = self._ctl().install_unit(
                    src, scope=SystemdScope.USER, daemon_reload=True
                )
                assert result.unit_name == "web.service"
                assert result.scope == SystemdScope.USER
                assert result.destination.exists()
                assert result.daemon_reloaded is True
                assert result.enabled is False
                mock_reload.assert_called_once()

    def test_install_unit_user_scope_with_enable(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "web.service"
            src.write_text("[Unit]\nDescription=Test\n")
            dest_dir = Path(tmp) / "user_units"
            with (
                mock.patch.object(SystemdScope.USER, "location", [dest_dir]),
                mock.patch.object(SubprocessSystemdCtl, "daemon_reload"),
                mock.patch.object(SubprocessSystemdCtl, "enable") as mock_enable,
            ):
                result = self._ctl().install_unit(
                    src, scope=SystemdScope.USER, enable=True, daemon_reload=False
                )
                assert result.enabled is True
                assert result.daemon_reloaded is False
                mock_enable.assert_called_once_with(
                    "web.service", scope=SystemdScope.USER
                )

    def test_install_unit_system_scope_no_permission_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "web.service"
            src.write_text("[Unit]\nDescription=Test\n")
            dest_dir = Path(tmp) / "sys_units"
            dest_dir.mkdir()
            with (
                mock.patch.object(SystemdScope.SYSTEM, "location", [dest_dir]),
                mock.patch.object(SubprocessSystemdCtl, "daemon_reload"),
            ):
                result = self._ctl().install_unit(src, scope=SystemdScope.SYSTEM)
                assert result.destination.exists()

    @mock.patch("subprocess.run")
    def test_install_unit_system_scope_permission_error(self, mock_run):
        """When the initial copy raises PermissionError, fall back to sudo cp."""
        mock_run.return_value = self._make_run_result(0, "", "")
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "web.service"
            src.write_text("[Unit]\nDescription=Test\n")
            dest_dir = Path(tmp) / "sys_units"
            dest_dir.mkdir()

            import shutil as _shutil

            call_count = 0
            real_copy2 = _shutil.copy2

            def _copy2_first_fails(s, d):
                nonlocal call_count
                call_count += 1
                if call_count == 1:
                    raise PermissionError("denied")
                return real_copy2(s, d)

            with (
                mock.patch.object(SystemdScope.SYSTEM, "location", [dest_dir]),
                mock.patch(
                    "django_systemd.protocol.shutil.copy2",
                    side_effect=_copy2_first_fails,
                ),
                mock.patch.object(SubprocessSystemdCtl, "daemon_reload"),
            ):
                self._ctl().install_unit(src, scope=SystemdScope.SYSTEM)
                calls = [str(c) for c in mock_run.call_args_list]
                assert any("cp" in c for c in calls)

    def test_install_unit_custom_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "web.service"
            src.write_text("[Unit]\nDescription=Test\n")
            dest_dir = Path(tmp) / "user_units"
            with (
                mock.patch.object(SystemdScope.USER, "location", [dest_dir]),
                mock.patch.object(SubprocessSystemdCtl, "daemon_reload"),
            ):
                result = self._ctl().install_unit(
                    src, scope=SystemdScope.USER, name="myapp.service"
                )
                assert result.unit_name == "myapp.service"

    # --- uninstall_unit ---

    def test_uninstall_unit_user_scope(self):
        with tempfile.TemporaryDirectory() as tmp:
            dest_dir = Path(tmp) / "user_units"
            dest_dir.mkdir()
            unit_file = dest_dir / "web.service"
            unit_file.write_text("[Unit]\n")
            with (
                mock.patch.object(SystemdScope.USER, "location", [dest_dir]),
                mock.patch.object(SubprocessSystemdCtl, "disable"),
                mock.patch.object(SubprocessSystemdCtl, "daemon_reload"),
            ):
                self._ctl().uninstall_unit("web.service", scope=SystemdScope.USER)
                assert not unit_file.exists()

    def test_uninstall_unit_no_disable(self):
        with tempfile.TemporaryDirectory() as tmp:
            dest_dir = Path(tmp) / "user_units"
            dest_dir.mkdir()
            unit_file = dest_dir / "web.service"
            unit_file.write_text("[Unit]\n")
            with (
                mock.patch.object(SystemdScope.USER, "location", [dest_dir]),
                mock.patch.object(SubprocessSystemdCtl, "disable") as mock_disable,
                mock.patch.object(SubprocessSystemdCtl, "daemon_reload"),
            ):
                self._ctl().uninstall_unit(
                    "web.service", scope=SystemdScope.USER, disable=False
                )
                mock_disable.assert_not_called()

    def test_uninstall_unit_file_not_found(self):
        """Should not raise if unit file is already gone."""
        with tempfile.TemporaryDirectory() as tmp:
            dest_dir = Path(tmp) / "user_units"
            dest_dir.mkdir()
            with (
                mock.patch.object(SystemdScope.USER, "location", [dest_dir]),
                mock.patch.object(SubprocessSystemdCtl, "disable"),
                mock.patch.object(SubprocessSystemdCtl, "daemon_reload"),
            ):
                # Should not raise
                self._ctl().uninstall_unit(
                    "nonexistent.service", scope=SystemdScope.USER
                )

    def test_uninstall_unit_no_daemon_reload(self):
        with tempfile.TemporaryDirectory() as tmp:
            dest_dir = Path(tmp) / "user_units"
            dest_dir.mkdir()
            with (
                mock.patch.object(SystemdScope.USER, "location", [dest_dir]),
                mock.patch.object(SubprocessSystemdCtl, "daemon_reload") as mock_reload,
                mock.patch.object(SubprocessSystemdCtl, "disable"),
            ):
                self._ctl().uninstall_unit(
                    "web.service", scope=SystemdScope.USER, daemon_reload=False
                )
                mock_reload.assert_not_called()

    @mock.patch("subprocess.run")
    def test_uninstall_unit_system_scope_permission_error(self, mock_run):
        mock_run.return_value = self._make_run_result(0, "", "")
        with tempfile.TemporaryDirectory() as tmp:
            dest_dir = Path(tmp) / "sys_units"
            dest_dir.mkdir()
            unit_file = dest_dir / "web.service"
            unit_file.write_text("[Unit]\n")
            with (
                mock.patch.object(SystemdScope.SYSTEM, "location", [dest_dir]),
                mock.patch.object(SubprocessSystemdCtl, "disable"),
                mock.patch.object(SubprocessSystemdCtl, "daemon_reload"),
                mock.patch.object(
                    Path, "unlink", side_effect=PermissionError("denied")
                ),
            ):
                # Should fall back to sudo rm
                self._ctl().uninstall_unit("web.service", scope=SystemdScope.SYSTEM)
                calls = [str(c) for c in mock_run.call_args_list]
                assert any("rm" in c for c in calls)

    @mock.patch("subprocess.run")
    def test_uninstall_unit_disable_ignores_error(self, mock_run):
        """CalledProcessError from disable should be silently swallowed."""
        mock_run.return_value = self._make_run_result(0, "", "")
        with tempfile.TemporaryDirectory() as tmp:
            dest_dir = Path(tmp) / "user_units"
            dest_dir.mkdir()
            with (
                mock.patch.object(SystemdScope.USER, "location", [dest_dir]),
                mock.patch.object(
                    SubprocessSystemdCtl,
                    "disable",
                    side_effect=subprocess.CalledProcessError(1, "systemctl"),
                ),
                mock.patch.object(SubprocessSystemdCtl, "daemon_reload"),
            ):
                # Should not raise
                self._ctl().uninstall_unit("web.service", scope=SystemdScope.USER)


# ---------------------------------------------------------------------------
# signals.py
# ---------------------------------------------------------------------------


class TestSignals:
    def test_unit_installed_signal_exists(self):
        from django_systemd.signals import unit_installed
        from django.dispatch import Signal

        assert isinstance(unit_installed, Signal)

    def test_unit_installed_signal_can_connect(self):
        from django_systemd.signals import unit_installed

        received = []

        def handler(sender, unit, **kwargs):
            received.append(unit)

        unit_installed.connect(handler)
        try:
            unit_installed.send(sender=object(), unit="web.service")
        finally:
            unit_installed.disconnect(handler)

        assert received == ["web.service"]


# ---------------------------------------------------------------------------
# Management command
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestSystemdCommand:
    def test_list_no_units(self, capsys):
        """When no templates exist, list should print the 'none found' message."""
        with override_settings(INSTALLED_APPS=["django_systemd", "django_typer"]):
            template_engine_config.cache_clear()
            render_engine.cache_clear()
            call_command("systemd", "list")
        out = capsys.readouterr().out
        assert "No systemd unit templates found" in out
        template_engine_config.cache_clear()
        render_engine.cache_clear()

    def test_list_outputs_unit_names(self, capsys):
        call_command("systemd", "list")
        out = capsys.readouterr().out
        assert "web.service" in out
        assert "check.timer" in out
        assert "app@.target" in out

    def test_list_shows_app2_paths(self, capsys):
        call_command("systemd", "list")
        out = capsys.readouterr().out
        assert "app2" in out

    def test_render_creates_files(self, capsys):
        with tempfile.TemporaryDirectory() as tmp:
            call_command("systemd", "render", tmp)
            files = {f.name for f in Path(tmp).rglob("*") if f.is_file()}
            assert "web.service" in files
            assert "check.timer" in files
            assert "app@.target" in files

    def test_render_default_dir(self, capsys, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        call_command("systemd", "render")
        files = {f.name for f in tmp_path.rglob("*") if f.is_file()}
        assert "web.service" in files

    def test_render_outputs_paths(self, capsys):
        with tempfile.TemporaryDirectory() as tmp:
            call_command("systemd", "render", tmp)
            out = capsys.readouterr().out
            assert "web.service" in out

    def test_render_no_templates_found(self, capsys):
        with override_settings(INSTALLED_APPS=["django_systemd", "django_typer"]):
            template_engine_config.cache_clear()
            render_engine.cache_clear()
            with tempfile.TemporaryDirectory() as tmp:
                call_command("systemd", "render", tmp)
            err = capsys.readouterr().err
            assert "No unit templates found" in err
        # Reset for other tests
        template_engine_config.cache_clear()
        render_engine.cache_clear()
