"""
Tests for the systemd management command. All systemctl interaction goes through
FakeCtl, so nothing here needs systemd installed.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest import mock

import pytest
from django.conf import settings
from django.core.management import CommandError, call_command
from django.test import override_settings

from django_systemd.config import ServiceUnit, render_engine, template_engine_config
from django_systemd.management.commands.systemd import Command, parse_context
from django_systemd.protocol import SystemdCtl
from django_systemd.signals import unit_installed


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
        # verb -> stderr text; when set, that verb raises CalledProcessError instead
        # of recording a call.
        self.fail: dict[str, str] = {}

    def _maybe_fail(self, verb: str, *args: str) -> None:
        if verb in self.fail:
            raise subprocess.CalledProcessError(
                1, ["systemctl", "--user", verb, *args], "", self.fail[verb]
            )

    def daemon_reload(self) -> None:
        self._maybe_fail("daemon-reload")
        self.calls.append(("daemon-reload", ""))

    def restart(self, *units: str) -> None:
        self._maybe_fail("restart", *units)
        self.calls.append(("restart", units))
        self.active.update(units)

    def reload(self, *units: str) -> None:
        self._maybe_fail("reload", *units)
        self.calls.append(("reload", units))

    def stop(self, unit: str) -> None:
        self._maybe_fail("stop", unit)
        self.calls.append(("stop", unit))

    def can_reload(self, unit: str) -> bool:
        return unit in self.reloadable

    def enable(self, unit: str) -> None:
        self._maybe_fail("enable", unit)
        self.calls.append(("enable", unit))
        self.enabled.add(unit)

    def disable(self, unit: str) -> None:
        self._maybe_fail("disable", unit)
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
        # Not recorded in self.calls: install ordering (e.g. daemon-reload coming
        # after every unit is copied) is asserted via daemon-reload's position.
        self.unit_dir.mkdir(parents=True, exist_ok=True)
        destination = self.unit_dir / (name or source.name)
        destination.write_bytes(source.read_bytes())
        return destination

    def uninstall_unit(self, name: str) -> bool:
        destination = self.unit_dir / name
        if destination.is_file():
            self.calls.append(("uninstall", name))
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
            SYSTEMD_TEMPLATES=["**/broken.service"],
        ):
            template_engine_config.cache_clear()
            render_engine.cache_clear()
            with pytest.raises(CommandError, match="broken.service"):
                call_command("systemd", "render", str(tmp_path))
        assert not (tmp_path / "broken.service").exists()

    def test_non_template_error_still_cleans_up(self, fake_ctl, tmp_path):
        from django.conf import settings
        from django.urls import NoReverseMatch

        apps = ["tests.apps.app3", *settings.INSTALLED_APPS]
        with override_settings(
            INSTALLED_APPS=apps, SYSTEMD_TEMPLATES=["**/runtime.service"]
        ):
            template_engine_config.cache_clear()
            render_engine.cache_clear()
            with pytest.raises(NoReverseMatch):
                call_command("systemd", "render", str(tmp_path))
        assert not (tmp_path / "runtime.service").exists()

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


@pytest.mark.django_db
class TestInstall:
    def test_installs_every_unit_and_reloads_once(self, fake_ctl, capsys):
        call_command("systemd", "install")
        installed = {f.name for f in fake_ctl.unit_dir.iterdir()}
        assert installed == {"web.service", "check.timer", "app@.target"}
        assert "app2 override" in (fake_ctl.unit_dir / "web.service").read_text()
        assert fake_ctl.calls == [("daemon-reload", "")]
        out = capsys.readouterr().out
        assert str(fake_ctl.unit_dir / "web.service") in out

    def test_rerun_updates_in_place(self, fake_ctl):
        call_command("systemd", "install")
        (fake_ctl.unit_dir / "web.service").write_text("stale")
        call_command("systemd", "install")
        assert "stale" not in (fake_ctl.unit_dir / "web.service").read_text()

    def test_enable_skips_template_units(self, fake_ctl):
        call_command("systemd", "install", "--enable")
        enabled = {unit for verb, unit in fake_ctl.calls if verb == "enable"}
        assert enabled == {"web.service", "check.timer"}
        assert fake_ctl.calls.index(("daemon-reload", "")) < fake_ctl.calls.index(
            ("enable", "web.service")
        )

    def test_context_overrides(self, fake_ctl):
        call_command("systemd", "install", "-c", "venv=/srv/app/.venv")
        assert (
            "WorkingDirectory=/srv/app/.venv"
            in (fake_ctl.unit_dir / "web.service").read_text()
        )

    def test_source_dir_skips_rendering(self, fake_ctl, tmp_path):
        source = tmp_path / "prerendered"
        source.mkdir()
        for name in ("web.service", "check.timer", "app@.target"):
            (source / name).write_text(f"prerendered {name}")
        call_command("systemd", "install", "--source", str(source))
        assert (
            fake_ctl.unit_dir / "web.service"
        ).read_text() == "prerendered web.service"

    def test_source_dir_missing_unit_errors(self, fake_ctl, tmp_path):
        source = tmp_path / "prerendered"
        source.mkdir()
        (source / "web.service").write_text("x")
        with pytest.raises(CommandError, match="check.timer"):
            call_command("systemd", "install", "--source", str(source))
        assert not fake_ctl.unit_dir.exists()

    def test_sends_unit_installed(self, fake_ctl):
        received: list[dict] = []

        def receiver(sender, **kwargs):
            received.append({"sender": sender, **kwargs})

        unit_installed.connect(receiver)
        try:
            call_command("systemd", "install")
        finally:
            unit_installed.disconnect(receiver)
        assert len(received) == 3
        for event in received:
            assert isinstance(event["sender"], Command)
            assert isinstance(event["unit"], ServiceUnit)
            assert event["destination"] == fake_ctl.unit_dir / event["unit"].filename

    def test_no_units_errors(self, fake_ctl, no_units):
        with pytest.raises(CommandError, match="No systemd unit templates"):
            call_command("systemd", "install")

    def test_without_systemctl_still_copies(self, make_ctl, capsys):
        ctl = make_ctl(available=False)
        call_command("systemd", "install", "--enable")
        assert (ctl.unit_dir / "web.service").is_file()
        assert ctl.calls == []
        assert "systemctl not found" in capsys.readouterr().err

    def test_source_with_context_is_an_error(self, fake_ctl, tmp_path):
        source = tmp_path / "prerendered"
        source.mkdir()
        with pytest.raises(CommandError, match="--context"):
            call_command(
                "systemd",
                "install",
                "--source",
                str(source),
                "-c",
                "venv=/x",
            )

    def test_daemon_reload_failure_is_a_command_error(self, fake_ctl):
        fake_ctl.fail = {"daemon-reload": "Failed to connect to bus: No medium found"}
        with pytest.raises(CommandError, match="Failed to connect to bus"):
            call_command("systemd", "install")
        installed = {f.name for f in fake_ctl.unit_dir.iterdir()}
        assert installed == {"web.service", "check.timer", "app@.target"}

    def test_enable_failure_is_a_command_error(self, fake_ctl):
        fake_ctl.fail = {"enable": "Unit has no installation config"}
        with pytest.raises(CommandError, match="no installation config"):
            call_command("systemd", "install", "--enable")

    def test_install_failure_names_the_unit(self, fake_ctl):
        original = fake_ctl.install_unit
        state = {"calls": 0, "failing_name": None}

        def flaky(source, **kwargs):
            state["calls"] += 1
            if state["calls"] == 2:
                state["failing_name"] = source.name
                raise OSError("disk full")
            return original(source, **kwargs)

        fake_ctl.install_unit = flaky  # type: ignore[method-assign]
        with pytest.raises(CommandError) as exc_info:
            call_command("systemd", "install")
        message = str(exc_info.value)
        # The second install_unit call is the one that fails; assert the exact
        # name that raised appears in the error, not just any project unit name.
        assert state["failing_name"] is not None
        assert state["failing_name"] in message
        assert "disk full" in message
        assert ("daemon-reload", "") not in fake_ctl.calls

    def test_empty_stderr_reports_exit_status(self, fake_ctl):
        fake_ctl.fail = {"daemon-reload": ""}
        with pytest.raises(CommandError, match="exit status 1"):
            call_command("systemd", "install")


@pytest.mark.django_db
class TestUninstall:
    def test_disables_removes_reloads(self, fake_ctl, capsys):
        call_command("systemd", "install", "--enable")
        fake_ctl.calls.clear()
        call_command("systemd", "uninstall")
        assert not any(fake_ctl.unit_dir.iterdir())
        disabled = {unit for verb, unit in fake_ctl.calls if verb == "disable"}
        assert disabled == {"web.service", "check.timer"}
        assert fake_ctl.calls.index(("stop", "web.service")) < fake_ctl.calls.index(
            ("disable", "web.service")
        )
        assert fake_ctl.calls.index(("disable", "web.service")) < fake_ctl.calls.index(
            ("uninstall", "web.service")
        )
        target_calls = [call for call in fake_ctl.calls if call[1] == "app@.target"]
        assert target_calls == [("uninstall", "app@.target")]
        assert fake_ctl.calls[-1] == ("daemon-reload", "")
        out = capsys.readouterr().out
        assert "web.service" in out

    def test_nothing_installed_is_a_noop(self, fake_ctl, capsys):
        call_command("systemd", "uninstall")
        assert "removed" not in capsys.readouterr().out
        assert fake_ctl.calls[-1] == ("daemon-reload", "")
        assert not any(verb in ("stop", "disable") for verb, _ in fake_ctl.calls)

    def test_stop_or_disable_failure_is_reported_not_fatal(self, fake_ctl, capsys):
        fake_ctl.fail = {"disable": "Unit not enabled"}
        call_command("systemd", "install")
        call_command("systemd", "uninstall")
        assert not any(fake_ctl.unit_dir.iterdir())
        assert ("daemon-reload", "") in fake_ctl.calls
        assert "Unit not enabled" in capsys.readouterr().err

    def test_without_systemctl_still_removes(self, make_ctl, capsys):
        ctl = make_ctl(available=False)
        call_command("systemd", "install")
        call_command("systemd", "uninstall")
        assert not any(ctl.unit_dir.iterdir())
        # "uninstall" is FakeCtl's own bookkeeping for file removal, not a
        # systemctl verb (mirrors uninstall_unit, which never shells out).
        assert all(verb == "uninstall" for verb, _ in ctl.calls)
        assert "systemctl not found" in capsys.readouterr().err


class TestParseContext:
    def test_value_contains_equals(self):
        assert parse_context(["KEY=a=b"]) == {"KEY": "a=b"}

    def test_duplicate_key_last_wins(self):
        assert parse_context(["KEY=a", "KEY=b"]) == {"KEY": "b"}

    def test_empty_value(self):
        assert parse_context(["KEY="]) == {"KEY": ""}

    def test_key_is_stripped(self):
        assert parse_context([" venv =x"]) == {"venv": "x"}


@pytest.mark.django_db
class TestRestart:
    def test_restarts_installed_units_in_order(self, fake_ctl, capsys):
        call_command("systemd", "install")
        fake_ctl.calls.clear()
        call_command("systemd", "restart")
        assert fake_ctl.calls == [("restart", ("web.service", "check.timer"))]
        out = capsys.readouterr().out
        assert "restarted web.service check.timer" in out

    def test_only_installed_units_by_default(self, fake_ctl):
        call_command("systemd", "install")
        fake_ctl.uninstall_unit("check.timer")
        fake_ctl.calls.clear()
        call_command("systemd", "restart")
        assert fake_ctl.calls == [("restart", ("web.service",))]

    def test_explicit_subset(self, fake_ctl):
        call_command("systemd", "restart", "check.timer")
        assert fake_ctl.calls == [("restart", ("check.timer",))]

    def test_explicit_units_are_ordered(self, fake_ctl):
        call_command("systemd", "restart", "check.timer", "web.service")
        assert fake_ctl.calls == [("restart", ("web.service", "check.timer"))]

    def test_duplicate_explicit_units_are_deduped(self, fake_ctl):
        call_command("systemd", "restart", "web.service", "web.service")
        assert fake_ctl.calls == [("restart", ("web.service",))]

    def test_unknown_unit(self, fake_ctl):
        with pytest.raises(CommandError, match="nope.service"):
            call_command("systemd", "restart", "nope.service")
        assert fake_ctl.calls == []

    def test_template_unit_is_not_a_target(self, fake_ctl):
        with pytest.raises(CommandError, match="app@.target"):
            call_command("systemd", "restart", "app@.target")

    def test_nothing_installed_is_a_noop(self, fake_ctl, capsys):
        call_command("systemd", "restart")
        assert fake_ctl.calls == []
        captured = capsys.readouterr()
        assert "restarted" not in captured.out
        assert "No installed project units" in captured.err

    def test_failure_is_a_command_error(self, fake_ctl):
        fake_ctl.fail = {"restart": "Job for web.service failed"}
        with pytest.raises(CommandError, match="Job for web.service failed"):
            call_command("systemd", "restart", "web.service")

    def test_requires_systemctl(self, make_ctl):
        make_ctl(available=False)
        with pytest.raises(CommandError, match="systemctl"):
            call_command("systemd", "restart")

    def test_socket_and_service_restart_in_one_transaction(self, fake_ctl):
        with override_settings(
            INSTALLED_APPS=["tests.apps.app3", *settings.INSTALLED_APPS],
            SYSTEMD_TEMPLATES=["**/web.socket", "**/web.service"],
        ):
            template_engine_config.cache_clear()
            render_engine.cache_clear()
            call_command("systemd", "install")
            fake_ctl.calls.clear()
            call_command("systemd", "restart")
        assert fake_ctl.calls == [("restart", ("web.socket", "web.service"))]


@pytest.mark.django_db
class TestReload:
    def test_reloads_when_supported_else_restarts(self, fake_ctl, capsys):
        fake_ctl.reloadable.add("web.service")
        call_command("systemd", "install")
        fake_ctl.active.add("web.service")
        fake_ctl.calls.clear()
        call_command("systemd", "reload")
        actions = [c for c in fake_ctl.calls if c[0] in {"restart", "reload"}]
        assert actions == [
            ("restart", ("check.timer",)),
            ("reload", ("web.service",)),
        ]
        out = capsys.readouterr().out
        assert "reloaded web.service" in out
        assert "restarted check.timer" in out

    def test_reload_restarts_before_reloading(self, fake_ctl):
        fake_ctl.reloadable.add("web.service")
        fake_ctl.active.add("web.service")
        call_command("systemd", "install")
        fake_ctl.calls.clear()
        call_command("systemd", "reload")
        actions = [c for c in fake_ctl.calls if c[0] in {"restart", "reload"}]
        assert actions == [
            ("restart", ("check.timer",)),
            ("reload", ("web.service",)),
        ]

    def test_reload_only_no_restart_call(self, fake_ctl):
        fake_ctl.reloadable.add("web.service")
        fake_ctl.active.add("web.service")
        call_command("systemd", "reload", "web.service")
        actions = [c for c in fake_ctl.calls if c[0] in {"restart", "reload"}]
        assert actions == [("reload", ("web.service",))]

    def test_reload_inactive_service_is_restarted(self, fake_ctl):
        fake_ctl.reloadable.add("web.service")
        call_command("systemd", "install")
        fake_ctl.calls.clear()
        call_command("systemd", "reload")
        actions = [c for c in fake_ctl.calls if c[0] in {"restart", "reload"}]
        assert actions == [("restart", ("web.service", "check.timer"))]

    def test_socket_is_kept_when_service_reloads(self, fake_ctl, capsys):
        with override_settings(
            INSTALLED_APPS=["tests.apps.app3", *settings.INSTALLED_APPS],
            SYSTEMD_TEMPLATES=["**/web.socket", "**/web.service"],
        ):
            template_engine_config.cache_clear()
            render_engine.cache_clear()
            call_command("systemd", "install")
            fake_ctl.calls.clear()
            fake_ctl.reloadable.add("web.service")
            fake_ctl.active.add("web.service")
            call_command("systemd", "reload")
        actions = [c for c in fake_ctl.calls if c[0] in {"restart", "reload"}]
        assert actions == [("reload", ("web.service",))]
        assert "kept web.socket" in capsys.readouterr().out

    def test_reload_never_queries_non_services(self, fake_ctl):
        fake_ctl.reloadable.add("web.service")
        fake_ctl.active.update({"web.service", "check.timer"})
        call_command("systemd", "install")
        fake_ctl.calls.clear()
        original_can_reload = fake_ctl.can_reload
        calls: list[str] = []

        def recording_can_reload(unit):
            calls.append(unit)
            return original_can_reload(unit)

        fake_ctl.can_reload = recording_can_reload  # type: ignore[method-assign]
        call_command("systemd", "reload")
        assert calls == ["web.service"]

    def test_nothing_installed_is_a_noop(self, fake_ctl, capsys):
        call_command("systemd", "reload")
        actions = [c for c in fake_ctl.calls if c[0] in {"restart", "reload"}]
        assert actions == []
        captured = capsys.readouterr()
        assert "reloaded" not in captured.out
        assert "restarted" not in captured.out
        assert "No installed project units" in captured.err

    def test_unknown_unit(self, fake_ctl):
        with pytest.raises(CommandError, match="nope.service"):
            call_command("systemd", "reload", "nope.service")

    def test_failure_is_a_command_error(self, fake_ctl):
        fake_ctl.reloadable.add("web.service")
        fake_ctl.active.add("web.service")
        fake_ctl.fail = {"reload": "Failed to reload web.service"}
        with pytest.raises(CommandError, match="Failed to reload web.service"):
            call_command("systemd", "reload", "web.service")

    def test_requires_systemctl(self, make_ctl):
        make_ctl(available=False)
        with pytest.raises(CommandError, match="systemctl"):
            call_command("systemd", "reload")
