"""
The systemd command is a Django_ :doc:`management command <django:ref/django-admin>`
that renders, installs, removes and manages systemd units.

.. typer:: django_systemd.management.commands.systemd.Command:typer_app
    :prog: django-admin systemd
    :width: 80
    :convert-png: latex
"""

from __future__ import annotations

from functools import cached_property
from pathlib import Path
from typing import Annotated, Optional

import typer
from django_typer.management import TyperCommand, command, initialize


class Command(TyperCommand):
    @cached_property
    def render_engine(self):
        from django_systemd.config import render_engine

        return render_engine()

    @initialize()
    def init(self) -> None:
        # Materialise and deduplicate by name; first occurrence wins (app precedence).
        seen: set[str] = set()
        self.units = []
        for tmpl in self.render_engine.search(""):
            name = tmpl.name
            if name is not None and name not in seen:
                seen.add(name)
                self.units.append(tmpl)

    @command()
    def list(self) -> None:
        """List available systemd unit templates."""
        if not self.units:
            typer.echo("No systemd unit templates found.")
            return
        for tmpl in self.units:
            typer.echo(f"{tmpl.name:<40} {tmpl.origin}")

    @command()
    def render(
        self,
        output_dir: Annotated[
            Optional[Path],
            typer.Argument(help="Directory to render templates into."),
        ] = None,
    ) -> None:
        """Render systemd unit templates to a directory."""
        from django.template.exceptions import TemplateDoesNotExist

        from django_systemd.config import template_engine_config

        dest = output_dir or Path(".")
        dest.mkdir(parents=True, exist_ok=True)
        patterns: list[str] = template_engine_config()["templates"]
        rendered = 0
        for pattern in patterns:
            try:
                for r in self.render_engine.render_each(pattern, dest=str(dest)):
                    typer.echo(str(r.destination))
                    rendered += 1
            except TemplateDoesNotExist:
                pass
        if not rendered:
            typer.echo("No unit templates found.", err=True)
