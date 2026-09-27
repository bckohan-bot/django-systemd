"""
Tests for django-systemd: config, defines, and signals.
"""

import sys
from pathlib import Path

import pytest
from django.conf import settings
from django.template.exceptions import TemplateDoesNotExist
from django.test import override_settings

from django_systemd.config import (
    SERVICE_UNIT_REGEX,
    ServiceUnit,
    project_units,
    render_engine,
    template_engine_config,
)
from django_systemd.defines import (
    SystemdRestartType,
    SystemdStartupType,
    SystemdUnitType,
)


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

    def test_value_is_the_literal_string(self):
        assert SystemdUnitType.SERVICE.value == "service"
        assert SystemdUnitType.SERVICE.literal == "service"
        assert repr(SystemdUnitType.SERVICE) == "<SystemdUnitType.SERVICE: 'service'>"

    def test_lookup_by_value(self):
        assert SystemdUnitType("timer") is SystemdUnitType.TIMER
        with pytest.raises(ValueError):
            SystemdUnitType("nope")
        assert SystemdUnitType.from_literal("timer") is SystemdUnitType.TIMER
        assert SystemdStartupType.from_literal("notify-reload") is (
            SystemdStartupType.NOTIFY_RELOAD
        )
        with pytest.raises(ValueError):
            SystemdUnitType.from_literal("nope")

    def test_members_are_hashable_and_distinct(self):
        lookup = {SystemdUnitType.SERVICE: 1, SystemdUnitType.TIMER: 2}
        assert lookup[SystemdUnitType.TIMER] == 2
        assert SystemdUnitType.SERVICE != SystemdUnitType.TIMER
        assert SystemdUnitType.SERVICE != "service"


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
        assert unit.path is None
        assert unit.instanceable is False

    def test_parse_path_sets_path(self):
        source = Path("/somewhere/systemd/check.timer")
        unit = ServiceUnit.parse(source)
        assert unit.name == "check"
        assert unit.unit_type == SystemdUnitType.TIMER
        assert unit.path == source

    def test_parse_instanceable(self):
        unit = ServiceUnit.parse("app@.target")
        assert unit.name == "app@"
        assert unit.instanceable is True

    def test_parse_invalid_raises(self):
        with pytest.raises(ValueError):
            ServiceUnit.parse("notes.txt")

    def test_parse_invalid_no_ext_raises(self):
        with pytest.raises(ValueError):
            ServiceUnit.parse("web")

    def test_filename(self):
        assert ServiceUnit("web", SystemdUnitType.SERVICE).filename == "web.service"
        assert ServiceUnit("app@", SystemdUnitType.TARGET).filename == "app@.target"

    def test_restart_priority_order(self):
        def prio(unit_type):
            return ServiceUnit("x", unit_type).restart_priority

        assert prio(SystemdUnitType.SOCKET) < prio(SystemdUnitType.SERVICE)
        assert prio(SystemdUnitType.SERVICE) < prio(SystemdUnitType.PATH)
        assert prio(SystemdUnitType.PATH) < prio(SystemdUnitType.TIMER)
        assert prio(SystemdUnitType.TIMER) < prio(SystemdUnitType.TARGET)
        assert prio(SystemdUnitType.TARGET) == prio(SystemdUnitType.MOUNT)

    def test_dotted_name(self):
        unit = ServiceUnit.parse("my.app.timer")
        assert unit.name == "my.app"
        assert unit.unit_type == SystemdUnitType.TIMER

    def test_template_defaults_to_filename(self):
        assert ServiceUnit.parse("web.service").template == "web.service"
        assert (
            ServiceUnit(
                "web", SystemdUnitType.SERVICE, template="sub/web.service"
            ).template
            == "sub/web.service"
        )

    def test_instance_is_not_instanceable(self):
        assert ServiceUnit.parse("worker@1.service").instanceable is False
        assert ServiceUnit.parse("worker@.service").instanceable is True


@pytest.mark.django_db
class TestProjectUnits:
    def test_discovers_all_units(self):
        names = sorted(unit.filename for unit in project_units())
        assert names == ["app@.target", "check.timer", "web.service"]

    def test_highest_precedence_app_wins(self):
        for unit in project_units():
            assert unit.path is not None
            assert unit.path.parent.parent.name == "app2"
            assert unit.path.is_file()

    def test_no_duplicates(self):
        names = [unit.filename for unit in project_units()]
        assert len(names) == len(set(names))

    def test_instanceable_flag(self):
        by_name = {unit.filename: unit for unit in project_units()}
        assert by_name["app@.target"].instanceable is True
        assert by_name["web.service"].instanceable is False

    def test_empty_when_no_apps_provide_units(self):
        with override_settings(INSTALLED_APPS=["django_systemd", "django_typer"]):
            template_engine_config.cache_clear()
            render_engine.cache_clear()
            assert project_units() == []

    def test_nested_dotted_and_invalid_names(self, caplog):
        apps = ["tests.apps.app3", *settings.INSTALLED_APPS]
        with override_settings(INSTALLED_APPS=apps):
            template_engine_config.cache_clear()
            render_engine.cache_clear()
            with caplog.at_level("WARNING", logger="django_systemd.config"):
                units = project_units()
        names = [unit.filename for unit in units]
        # nested sub/web.service collapses with the top-level web.service
        assert names.count("web.service") == 1
        assert len(names) == len(set(names))
        by_name = {unit.filename: unit for unit in units}
        assert by_name["my.app.timer"].name == "my.app"
        assert "bad name.service" not in by_name
        assert any("bad name.service" in rec.message for rec in caplog.records)

    def test_cross_name_shadowing_warns(self, caplog):
        apps = ["tests.apps.app3", *settings.INSTALLED_APPS]
        with override_settings(
            INSTALLED_APPS=apps, SYSTEMD_TEMPLATES=["**/web.service"]
        ):
            template_engine_config.cache_clear()
            render_engine.cache_clear()
            with caplog.at_level("WARNING", logger="django_systemd.config"):
                units = project_units()
        assert [u.filename for u in units] == ["web.service"]
        assert any("already provided by template" in r.message for r in caplog.records)


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

    def test_render_service_template(self, tmp_path):
        """Rendered service file should contain rendered context variables."""
        engine = render_engine()
        renders = list(engine.render_each("**/*.service", dest=tmp_path))
        assert len(renders) == 1
        content = Path(renders[0].destination).read_text()
        # Template variable {{ python }} should be substituted
        assert str(sys.executable) in content
        # app2 override marker should appear
        assert "app2 override" in content

    def test_render_timer_template(self, tmp_path):
        engine = render_engine()
        renders = list(engine.render_each("**/*.timer", dest=tmp_path))
        assert len(renders) == 1
        content = Path(renders[0].destination).read_text()
        assert "app2 override" in content

    def test_render_unknown_pattern_raises(self):
        engine = render_engine()
        with pytest.raises(TemplateDoesNotExist):
            list(engine.render_each("**/*.socket", dest="/tmp"))


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
