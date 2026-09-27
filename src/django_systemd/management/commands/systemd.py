"""
The systemd command is a Django_ :doc:`management command <django:ref/django-admin>`
that renders, installs, and restarts the systemd units bundled by your project's
apps. Every subcommand works from the same manifest, the unit templates discovered
in each installed app's ``systemd/`` directory, so a deployment never has to
hard-code unit names.

All units are managed in the **user** scope (``systemctl --user``). Nothing here
runs as root.

.. typer:: django_systemd.management.commands.systemd.Command:typer_app
    :prog: django-admin systemd
    :width: 80
    :convert-png: latex
"""

from __future__ import annotations

import subprocess
import tempfile
from collections.abc import Callable
from functools import cached_property
from pathlib import Path
from typing import Annotated

import typer
from django.core.management import CommandError
from django.template import TemplateDoesNotExist, TemplateSyntaxError
from django_typer.management import TyperCommand, command

from django_systemd.config import ServiceUnit, project_units, render_engine
from django_systemd.defines import SystemdUnitType
from django_systemd.protocol import SubprocessSystemdCtl, SystemdCtl
from django_systemd.signals import unit_installed

ContextOption = Annotated[
    list[str] | None,
    typer.Option(
        "--context",
        "-c",
        help="Override a template context variable as KEY=VALUE. May be repeated.",
    ),
]

UnitsArgument = Annotated[
    list[str] | None,
    typer.Argument(
        help="Unit file names to act on. Defaults to every installed project unit."
    ),
]


def describe_failure(err: subprocess.CalledProcessError) -> str:
    """One line naming the systemctl invocation and why it failed."""
    detail = (err.stderr or "").strip() or f"exit status {err.returncode}"
    return f"{' '.join(err.cmd)} failed: {detail}"


def parse_context(pairs: list[str]) -> dict[str, str]:
    """Turn ``["venv=/srv/app"]`` into ``{"venv": "/srv/app"}``."""
    context: dict[str, str] = {}
    for pair in pairs:
        key, sep, value = pair.partition("=")
        key = key.strip()
        if not sep or not key:
            raise CommandError(f"Context overrides must be KEY=VALUE, got: {pair!r}")
        context[key] = value
    return context


class Command(TyperCommand):
    @cached_property
    def ctl(self) -> SystemdCtl:
        return SubprocessSystemdCtl()

    @cached_property
    def units(self) -> list[ServiceUnit]:
        return project_units()

    def run_ctl(self, verb: Callable[..., None], *args: str) -> None:
        """Run a systemctl-backed verb, surfacing stderr on failure."""
        try:
            verb(*args)
        except subprocess.CalledProcessError as err:
            raise CommandError(describe_failure(err)) from err

    def require_systemctl(self) -> None:
        """Refuse to proceed when systemctl is not available on this system."""
        if not self.ctl.available:
            raise CommandError("systemctl is not available on this system.")

    def targets(self, names: list[str]) -> list[ServiceUnit]:
        """
        Resolve unit names to project units.

        The priority sort only makes the invocation and its output
        deterministic; systemd orders the jobs itself within the transaction.
        Template units (``name@.type``) are never targets because systemctl needs
        an instance name to act on them. With no names, every installed
        non-template unit is selected, except that a service with a timer or
        path unit of the same name is dropped: that service is triggered by
        its timer or path (typically a oneshot job) and restarting it directly
        would run the job, not just make it ready to run. Naming the service
        explicitly still restarts it.
        """
        by_name = {u.filename: u for u in self.units if not u.instanceable}
        if names:
            unknown = [name for name in names if name not in by_name]
            if unknown:
                raise CommandError(f"Unknown project unit(s): {', '.join(unknown)}")
            selected = [by_name[n] for n in dict.fromkeys(names)]
        else:
            selected = [
                u for u in by_name.values() if self.ctl.is_installed(u.filename)
            ]
            triggered = {
                u.name
                for u in selected
                if u.unit_type in (SystemdUnitType.TIMER, SystemdUnitType.PATH)
            }
            selected = [
                u
                for u in selected
                if not (u.unit_type is SystemdUnitType.SERVICE and u.name in triggered)
            ]
        return sorted(selected, key=lambda u: u.restart_priority)

    def render_units(
        self, dest: Path, context: dict[str, str] | None = None
    ) -> list[tuple[ServiceUnit, Path]]:
        """Render every project unit into ``dest`` as ``<dest>/<unit filename>``."""
        if dest.exists() and not dest.is_dir():
            raise CommandError(f"{dest} exists and is not a directory.")
        if not self.units:
            raise CommandError("No systemd unit templates found.")
        dest.mkdir(parents=True, exist_ok=True)
        rendered: list[tuple[ServiceUnit, Path]] = []
        for unit in self.units:
            # Absolute: render-static 3.5 builds a Path from a SafeString, which
            # Python 3.10 rejects for a single relative path part.
            target = dest.absolute() / unit.filename
            try:
                for render in render_engine().render_each(
                    unit.template, dest=target, context=context or None
                ):
                    rendered.append((unit, Path(render.destination)))
            except (TemplateDoesNotExist, TemplateSyntaxError) as err:
                target.unlink(missing_ok=True)
                raise CommandError(f"Failed to render {unit.template}: {err}") from err
            except BaseException:
                # Anything else (e.g. NoReverseMatch from {% url %}) is a bug in the
                # template or the project and should surface with its traceback.
                target.unlink(missing_ok=True)
                raise
        return rendered

    @command(name="list")
    def list_units(self) -> None:
        """List this project's systemd units and whether each is installed, enabled and active."""
        if not self.units:
            typer.echo("No systemd unit templates found.")
            return
        width = max(len("UNIT"), *(len(u.filename) for u in self.units))
        typer.echo(
            f"{'UNIT':<{width}} {'INSTALLED':<10} {'ENABLED':<8} {'ACTIVE':<8} SOURCE"
        )
        available = self.ctl.available
        for unit in self.units:
            installed = self.ctl.is_installed(unit.filename)
            enabled = active = "-"
            if installed and available and not unit.instanceable:
                enabled = "yes" if self.ctl.is_enabled(unit.filename) else "no"
                active = "yes" if self.ctl.is_active(unit.filename) else "no"
            typer.echo(
                f"{unit.filename:<{width}} {'yes' if installed else 'no':<10} "
                f"{enabled:<8} {active:<8} {unit.path}"
            )

    @command()
    def render(
        self,
        output_dir: Annotated[
            Path | None,
            typer.Argument(
                help="Directory to render unit files into. Defaults to the current directory."
            ),
        ] = None,
        context: ContextOption = None,
    ) -> None:
        """Render this project's unit templates to a directory."""
        rendered = self.render_units(
            output_dir or Path("."), parse_context(context or [])
        )
        for _, path in rendered:
            typer.echo(str(path))

    @command()
    def install(
        self,
        source: Annotated[
            Path | None,
            typer.Option(
                "--source",
                help="Install pre-rendered unit files from this directory instead of rendering now.",
                exists=True,
                file_okay=False,
            ),
        ] = None,
        enable: Annotated[
            bool,
            typer.Option(
                "--enable/--no-enable", help="Enable the units after installing."
            ),
        ] = False,
        context: ContextOption = None,
    ) -> None:
        """
        Install this project's units into the user unit directory.

        Units are rendered first unless --source points at pre-rendered files.
        Running install again replaces the installed files, so this is also how
        you update units after a deploy.
        """
        if not self.units:
            raise CommandError("No systemd unit templates found.")
        if source is not None and context:
            raise CommandError("--context has no effect with --source.")

        with tempfile.TemporaryDirectory() as tmp:
            if source is None:
                files = self.render_units(Path(tmp), parse_context(context or []))
            else:
                # Pre-rendered units are flat files named by unit file name, exactly
                # as `systemd render` writes them.
                files = [(unit, source / unit.filename) for unit in self.units]
                missing = [path.name for _, path in files if not path.is_file()]
                if missing:
                    raise CommandError(
                        f"Missing unit files in {source}: {', '.join(missing)}"
                    )
            for unit, path in files:
                try:
                    destination = self.ctl.install_unit(path)
                except OSError as err:
                    raise CommandError(
                        f"Failed to install {unit.filename} to {self.ctl.unit_dir}: {err}. "
                        "Re-run install once the cause is fixed."
                    ) from err
                unit_installed.send(sender=self, unit=unit, destination=destination)
                typer.echo(str(destination))

        if not self.ctl.available:
            typer.secho(
                "systemctl not found; skipped daemon-reload and enable.", err=True
            )
            return
        self.run_ctl(self.ctl.daemon_reload)
        if enable:
            for unit in self.units:
                if not unit.instanceable:
                    self.run_ctl(self.ctl.enable, unit.filename)

    @command()
    def uninstall(self) -> None:
        """Stop, disable and remove this project's units from the user unit directory."""
        for unit in self.units:
            if (
                self.ctl.available
                and not unit.instanceable
                and self.ctl.is_installed(unit.filename)
            ):
                for verb in (self.ctl.stop, self.ctl.disable):
                    try:
                        verb(unit.filename)
                    except subprocess.CalledProcessError as err:
                        typer.secho(describe_failure(err), err=True)
            if self.ctl.uninstall_unit(unit.filename):
                typer.echo(f"removed {unit.filename}")
        if self.ctl.available:
            self.run_ctl(self.ctl.daemon_reload)
        else:
            typer.secho(
                "systemctl not found; skipped stop, disable and daemon-reload.",
                err=True,
            )

    @command()
    def restart(self, units: UnitsArgument = None) -> None:
        """
        Restart this project's units in a single systemctl transaction.

        Restarting sockets and services one at a time does not work: systemd
        refuses to start a socket whose service is still running. Passing every
        target to one systemctl invocation lets systemd order the stops and
        starts itself. A failure reports the whole invocation; systemctl's
        output names the job that failed.
        """
        self.require_systemctl()
        targets = self.targets(units or [])
        if not targets:
            typer.secho("No installed project units to restart.", err=True)
            return
        names = [unit.filename for unit in targets]
        self.run_ctl(self.ctl.restart, *names)
        typer.echo(f"restarted {' '.join(names)}")

    @command()
    def reload(self, units: UnitsArgument = None) -> None:
        """
        Reload services that support it and are running; restart everything else.

        Only services can define ExecReload=, and systemctl refuses to reload an
        inactive unit, so anything that is not an active reloadable service is
        restarted instead, in one systemctl transaction. A socket paired with a
        service that reloads in place is left as it is.
        """
        self.require_systemctl()
        targets = self.targets(units or [])
        if not targets:
            typer.secho("No installed project units to reload.", err=True)
            return
        to_reload: list[str] = []
        to_restart: list[ServiceUnit] = []
        for unit in targets:
            name = unit.filename
            if (
                unit.unit_type is SystemdUnitType.SERVICE
                and self.ctl.can_reload(name)
                and self.ctl.is_active(name)
            ):
                to_reload.append(name)
            else:
                to_restart.append(unit)
        # A listening socket whose service is reloaded in place needs no action,
        # and systemd would refuse to restart it while the service runs.
        kept = [
            u
            for u in to_restart
            if u.unit_type is SystemdUnitType.SOCKET
            and f"{u.name}.service" in to_reload
        ]
        to_restart = [u for u in to_restart if u not in kept]
        for unit in kept:
            typer.echo(
                f"kept {unit.filename} listening; {unit.name}.service reloads in place"
            )
        if to_restart:
            names = [u.filename for u in to_restart]
            self.run_ctl(self.ctl.restart, *names)
            typer.echo(f"restarted {' '.join(names)}")
        if to_reload:
            self.run_ctl(self.ctl.reload, *to_reload)
            typer.echo(f"reloaded {' '.join(to_reload)}")
