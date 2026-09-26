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

from functools import cached_property
from pathlib import Path
from typing import Annotated

import typer
from django.core.management import CommandError
from django.template.exceptions import TemplateDoesNotExist
from django_typer.management import TyperCommand, command

from django_systemd.config import (
    ServiceUnit,
    project_units,
    render_engine,
    template_engine_config,
)
from django_systemd.protocol import SubprocessSystemdCtl, SystemdCtl

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
    ) -> list[Path]:
        """Render every project unit into ``dest`` and return the written paths."""
        dest.mkdir(parents=True, exist_ok=True)
        rendered: list[Path] = []
        for pattern in template_engine_config()["templates"]:
            try:
                for render in render_engine().render_each(
                    pattern, dest=dest, context=context or None
                ):
                    rendered.append(Path(render.destination))
            except TemplateDoesNotExist:
                continue
        return rendered

    @command(name="list")
    def list_units(self) -> None:
        """List this project's systemd units and whether each is installed, enabled and active."""
        if not self.units:
            typer.echo("No systemd unit templates found.")
            return
        typer.echo(
            f"{'UNIT':<32} {'INSTALLED':<10} {'ENABLED':<8} {'ACTIVE':<8} SOURCE"
        )
        for unit in self.units:
            installed = self.ctl.is_installed(unit.filename)
            enabled = active = "-"
            if installed and self.ctl.available and not unit.instanceable:
                enabled = "yes" if self.ctl.is_enabled(unit.filename) else "no"
                active = "yes" if self.ctl.is_active(unit.filename) else "no"
            typer.echo(
                f"{unit.filename:<32} {'yes' if installed else 'no':<10} "
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
        for path in rendered:
            typer.echo(str(path))
        if not rendered:
            typer.echo("No unit templates found.", err=True)
