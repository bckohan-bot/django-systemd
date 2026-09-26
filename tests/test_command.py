"""
Tests for the systemd management command. All systemctl interaction goes through
FakeCtl, so nothing here needs systemd installed.
"""

from __future__ import annotations

from pathlib import Path
from unittest import mock

import pytest
from django.conf import settings
from django.core.management import CommandError, call_command
from django.test import override_settings

from django_systemd.config import render_engine, template_engine_config
from django_systemd.management.commands.systemd import parse_context
from django_systemd.protocol import SystemdCtl


class FakeCtl:
    """An in-memory SystemdCtl that records every verb it is asked to run."""

    def __init__(
        self,
        unit_dir: Path,
        *,
        available: bool = True,
        reloadable: set[str] | None = None,
    ) -> None:
        self.unit_dir = unit_dir
        self.available = available
        self.reloadable = reloadable or set()
        self.calls: list[tuple[str, str]] = []
        self.active: set[str] = set()
        self.enabled: set[str] = set()

    def daemon_reload(self) -> None:
        self.calls.append(("daemon-reload", ""))

    def restart(self, unit: str) -> None:
        self.calls.append(("restart", unit))
        self.active.add(unit)

    def reload(self, unit: str) -> None:
        self.calls.append(("reload", unit))

    def can_reload(self, unit: str) -> bool:
        return unit in self.reloadable

    def enable(self, unit: str) -> None:
        self.calls.append(("enable", unit))
        self.enabled.add(unit)

    def disable(self, unit: str) -> None:
        self.calls.append(("disable", unit))
        self.enabled.discard(unit)

    def is_active(self, unit: str) -> bool:
        self.calls.append(("is-active", unit))
        return unit in self.active

    def is_enabled(self, unit: str) -> bool:
        self.calls.append(("is-enabled", unit))
        return unit in self.enabled

    def is_installed(self, name: str) -> bool:
        return (self.unit_dir / name).is_file()

    def install_unit(
        self, source: Path, *, name: str | None = None, mode: int = 0o644
    ) -> Path:
        self.unit_dir.mkdir(parents=True, exist_ok=True)
        destination = self.unit_dir / (name or source.name)
        destination.write_bytes(source.read_bytes())
        return destination

    def uninstall_unit(self, name: str) -> bool:
        destination = self.unit_dir / name
        if destination.is_file():
            destination.unlink()
            return True
        return False


@pytest.fixture
def fake_ctl(tmp_path):
    ctl = FakeCtl(tmp_path / "units")
    with mock.patch(
        "django_systemd.management.commands.systemd.SubprocessSystemdCtl",
        return_value=ctl,
    ):
        yield ctl


@pytest.fixture
def make_ctl(tmp_path):
    def factory(**kwargs):
        ctl = FakeCtl(tmp_path / "units", **kwargs)
        patcher = mock.patch(
            "django_systemd.management.commands.systemd.SubprocessSystemdCtl",
            return_value=ctl,
        )
        patcher.start()
        patchers.append(patcher)
        return ctl

    patchers: list = []
    yield factory
    for patcher in patchers:
        patcher.stop()


@pytest.fixture
def no_units():
    """Run the body with no app providing systemd templates."""
    with override_settings(INSTALLED_APPS=["django_systemd", "django_typer"]):
        template_engine_config.cache_clear()
        render_engine.cache_clear()
        yield
    template_engine_config.cache_clear()
    render_engine.cache_clear()


def test_fake_ctl_satisfies_protocol(tmp_path):
    assert isinstance(FakeCtl(tmp_path), SystemdCtl)


@pytest.mark.django_db
class TestList:
    def test_no_units(self, fake_ctl, no_units, capsys):
        call_command("systemd", "list")
        assert "No systemd unit templates found" in capsys.readouterr().out

    def test_lists_every_project_unit_with_source(self, fake_ctl, capsys):
        call_command("systemd", "list")
        out = capsys.readouterr().out
        assert "UNIT" in out and "INSTALLED" in out
        for name in ("web.service", "check.timer", "app@.target"):
            assert name in out
        assert "app2" in out

    def test_not_installed_rows_do_not_query_systemctl(self, fake_ctl, capsys):
        call_command("systemd", "list")
        out = capsys.readouterr().out
        assert fake_ctl.calls == []
        row = next(line for line in out.splitlines() if line.startswith("web.service"))
        assert row.split()[1:4] == ["no", "-", "-"]

    def test_installed_rows_show_state(self, fake_ctl, capsys):
        fake_ctl.unit_dir.mkdir(parents=True)
        (fake_ctl.unit_dir / "web.service").write_text("x")
        fake_ctl.active.add("web.service")
        call_command("systemd", "list")
        out = capsys.readouterr().out
        row = next(line for line in out.splitlines() if line.startswith("web.service"))
        assert row.split()[1:4] == ["yes", "no", "yes"]

    def test_instanceable_units_are_never_queried(self, fake_ctl, capsys):
        fake_ctl.unit_dir.mkdir(parents=True)
        (fake_ctl.unit_dir / "app@.target").write_text("x")
        call_command("systemd", "list")
        out = capsys.readouterr().out
        row = next(line for line in out.splitlines() if line.startswith("app@.target"))
        assert row.split()[1:4] == ["yes", "-", "-"]
        assert fake_ctl.calls == []

    def test_unavailable_systemctl_shows_dashes(self, make_ctl, capsys):
        ctl = make_ctl(available=False)
        ctl.unit_dir.mkdir(parents=True)
        (ctl.unit_dir / "web.service").write_text("x")
        call_command("systemd", "list")
        row = next(
            line
            for line in capsys.readouterr().out.splitlines()
            if line.startswith("web.service")
        )
        assert row.split()[1:4] == ["yes", "-", "-"]


@pytest.mark.django_db
class TestRender:
    def test_creates_files(self, fake_ctl, tmp_path, capsys):
        call_command("systemd", "render", str(tmp_path))
        files = {f.name for f in tmp_path.rglob("*") if f.is_file()}
        assert files == {"web.service", "check.timer", "app@.target"}
        out = capsys.readouterr().out
        assert str(tmp_path / "web.service") in out

    def test_default_dir_is_cwd(self, fake_ctl, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        call_command("systemd", "render")
        assert (tmp_path / "web.service").is_file()

    def test_highest_precedence_content(self, fake_ctl, tmp_path):
        call_command("systemd", "render", str(tmp_path))
        assert "app2 override" in (tmp_path / "web.service").read_text()

    def test_context_overrides(self, fake_ctl, tmp_path):
        call_command(
            "systemd",
            "render",
            str(tmp_path),
            "-c",
            "venv=/srv/app/.venv",
            "--context",
            "python=/srv/app/.venv/bin/python",
        )
        content = (tmp_path / "web.service").read_text()
        assert "WorkingDirectory=/srv/app/.venv" in content
        assert "ExecStart=/srv/app/.venv/bin/python" in content

    def test_bad_context_pair(self, fake_ctl, tmp_path):
        with pytest.raises(CommandError, match="KEY=VALUE"):
            call_command("systemd", "render", str(tmp_path), "-c", "novalue")
        with pytest.raises(CommandError, match="KEY=VALUE"):
            call_command("systemd", "render", str(tmp_path), "-c", "=x")

    def test_no_templates(self, fake_ctl, no_units, tmp_path):
        with pytest.raises(CommandError, match="No systemd unit templates"):
            call_command("systemd", "render", str(tmp_path))

    def test_output_path_is_a_file(self, fake_ctl, tmp_path):
        target = tmp_path / "not-a-dir"
        target.write_text("x")
        with pytest.raises(CommandError, match="not a directory"):
            call_command("systemd", "render", str(target))

    def test_broken_template_errors_without_partial_output(self, fake_ctl, tmp_path):
        with override_settings(
            INSTALLED_APPS=["tests.apps.app3", *settings.INSTALLED_APPS],
            SYSTEMD_TEMPLATES=["**/*.service"],
        ):
            template_engine_config.cache_clear()
            render_engine.cache_clear()
            with pytest.raises(CommandError, match="broken.service"):
                call_command("systemd", "render", str(tmp_path))
        assert not (tmp_path / "broken.service").exists()

    def test_systemd_templates_setting_scopes_render(self, fake_ctl, tmp_path):
        with override_settings(
            INSTALLED_APPS=["tests.apps.app3", *settings.INSTALLED_APPS],
            SYSTEMD_TEMPLATES=["**/*.timer"],
        ):
            template_engine_config.cache_clear()
            render_engine.cache_clear()
            call_command("systemd", "render", str(tmp_path))
        assert (tmp_path / "my.app.timer").is_file()
        assert (tmp_path / "check.timer").is_file()
        assert not any(p.is_dir() for p in tmp_path.iterdir())

    def test_nested_template_renders_flat(self, fake_ctl, tmp_path):
        with override_settings(
            INSTALLED_APPS=["tests.apps.app3", "django_systemd", "django_typer"],
            SYSTEMD_TEMPLATES=["**/web.service"],
        ):
            template_engine_config.cache_clear()
            render_engine.cache_clear()
            call_command("systemd", "render", str(tmp_path))
        content = (tmp_path / "web.service").read_text()
        assert "nested web (app3)" in content
        assert not (tmp_path / "sub").exists()


class TestParseContext:
    def test_value_contains_equals(self):
        assert parse_context(["KEY=a=b"]) == {"KEY": "a=b"}

    def test_duplicate_key_last_wins(self):
        assert parse_context(["KEY=a", "KEY=b"]) == {"KEY": "b"}

    def test_empty_value(self):
        assert parse_context(["KEY="]) == {"KEY": ""}

    def test_key_is_stripped(self):
        assert parse_context([" venv =x"]) == {"venv": "x"}
