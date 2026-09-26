import os
import re
import sys
import typing as t
from dataclasses import dataclass
from functools import cache
from pathlib import Path

from render_static.context import resolve_context
from render_static.engine import StaticTemplateEngine

from .defines import SystemdUnitType

unit_types = "|".join(re.escape(typ.value) for typ in SystemdUnitType)

SERVICE_UNIT_REGEX = re.compile(rf"^(?P<name>[\w@-]+)\.(?P<type>{unit_types})$")

# The order units should be restarted in. Sockets must be up before the services
# they activate, paths and timers trigger services so they go after. Anything not
# listed is restarted last.
_RESTART_ORDER: dict[SystemdUnitType, int] = {
    SystemdUnitType.SOCKET: 0,
    SystemdUnitType.SERVICE: 1,
    SystemdUnitType.PATH: 2,
    SystemdUnitType.TIMER: 3,
}


@dataclass
class ServiceUnit:
    """
    A systemd unit that belongs to this project.

    :param name: The unit name without its type suffix (e.g. ``web``).
    :param unit_type: The :class:`~django_systemd.defines.SystemdUnitType`.
    :param path: The template (or rendered file) this unit came from, if known.
    :param instanceable: True if this is a template unit (its name ends in ``@``).
    """

    name: str
    unit_type: SystemdUnitType
    path: Path | None = None
    instanceable: bool = False

    @property
    def filename(self) -> str:
        """The unit file name systemd knows this unit by, e.g. ``web.service``."""
        return f"{self.name}.{self.unit_type.value}"

    @property
    def restart_priority(self) -> int:
        """Lower values are restarted first."""
        return _RESTART_ORDER.get(self.unit_type, len(_RESTART_ORDER))

    @classmethod
    def parse(cls, raw: Path | str) -> "ServiceUnit":
        """
        Build a :class:`ServiceUnit` from a unit file name or path.

        :raises ValueError: if the name is not ``<name>.<unit type>``.
        """
        path = raw if isinstance(raw, Path) else None
        name = raw.name if isinstance(raw, Path) else raw
        if mtch := SERVICE_UNIT_REGEX.match(name):
            return cls(
                name=mtch.groupdict()["name"],
                unit_type=SystemdUnitType(mtch.groupdict()["type"]),
                path=path,
                instanceable="@" in name,
            )
        raise ValueError(f"Unrecognized unit name: '{name}'")


@cache
def template_engine_config() -> dict[str, t.Any]:
    """
    Get the configuration for the systemd template rendering engine.

    :return: The configuration dictionary for the rendering engine.
    :rtype: Dict[str, Any]
    """
    from django.conf import settings
    from django_typer.utils import get_usage_script

    engine_config = getattr(
        settings,
        "SYSTEMD_TEMPLATE_ENGINE",
        {
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
            ]
        },
    )
    engine_config.setdefault(
        "context", getattr(settings, "SYSTEMD_TEMPLATE_CONTEXT", {})
    )
    engine_config["context"] = resolve_context(engine_config["context"])
    engine_config["context"].setdefault("settings", settings)
    engine_config["context"].setdefault("venv", Path(sys.prefix))
    engine_config["context"].setdefault("python", Path(sys.executable))
    engine_config["context"].setdefault("django-admin", get_usage_script())
    engine_config.setdefault(
        "templates",
        getattr(
            settings,
            "SYSTEMD_TEMPLATES",
            [f"**/*.{unit_type}" for unit_type in SystemdUnitType],
        ),
    )
    engine_config["context"].setdefault(
        "DJANGO_SETTINGS_MODULE", os.environ.get("DJANGO_SETTINGS_MODULE", "")
    )
    return engine_config


@cache
def render_engine() -> StaticTemplateEngine:
    """
    Get the configured rendering engine for systemd service units.

    :return: Rendering engine that knows how to find and render systemd service unit
        templates.
    :rtype: :class:`~render_static.engine.StaticTemplateEngine`
    """
    return StaticTemplateEngine(template_engine_config())


def project_units() -> list[ServiceUnit]:
    """
    The manifest: every systemd unit template bundled by an installed app.

    Templates are yielded by the render engine in app precedence order, so when two
    apps provide the same unit name the first one wins and later ones are dropped.
    Files in a ``systemd/`` directory whose names are not ``<name>.<unit type>`` are
    ignored.

    :return: Units in discovery order, each with ``path`` set to its template.
    """
    seen: set[str] = set()
    units: list[ServiceUnit] = []
    for template in render_engine().search(""):
        name = template.name
        if not name or name in seen:
            continue
        try:
            unit = ServiceUnit.parse(Path(str(template.origin)))
        except ValueError:
            continue
        seen.add(name)
        units.append(unit)
    return units
