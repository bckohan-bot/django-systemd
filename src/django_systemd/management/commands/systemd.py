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
from functools import cached_property
from pathlib import Path
from typing import Annotated

import typer
from django.core.management import CommandError
from django.template import TemplateDoesNotExist, TemplateSyntaxError
from django_typer.management import TyperCommand, command

from django_systemd.config import ServiceUnit, project_units, render_engine
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
            target = dest / unit.filename
            try:
                for render in render_engine().render_each(
                    unit.template, dest=target, context=context or None
                ):
                    rendered.append((unit, Path(render.destination)))
            except Exception as err:
                target.unlink(missing_ok=True)
                if isinstance(err, (TemplateDoesNotExist, TemplateSyntaxError)):
                    raise CommandError(
                        f"Failed to render {unit.template}: {err}"
                    ) from err
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
                destination = self.ctl.install_unit(path)
                unit_installed.send(sender=self, unit=unit, destination=destination)
                typer.echo(str(destination))

        if not self.ctl.available:
            return
        self.ctl.daemon_reload()
        if enable:
            for unit in self.units:
                if not unit.instanceable:
                    self.ctl.enable(unit.filename)

    @command()
    def uninstall(self) -> None:
        """Disable and remove this project's units from the user unit directory."""
        for unit in self.units:
            if self.ctl.available and not unit.instanceable:
                try:
                    self.ctl.disable(unit.filename)
                except subprocess.CalledProcessError:
                    pass
            if self.ctl.uninstall_unit(unit.filename):
                typer.echo(f"removed {unit.filename}")
        if self.ctl.available:
            self.ctl.daemon_reload()
