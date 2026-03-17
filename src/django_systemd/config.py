import os
import re
import sys
import typing as t
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from render_static.context import resolve_context
from render_static.engine import StaticTemplateEngine

from .defines import SystemdUnitType

unit_types = "|".join(re.escape(typ.value) for typ in SystemdUnitType)

SERVICE_UNIT_REGEX = re.compile(rf"^(?P<name>[\w@-]+)\.(?P<type>{unit_types})$")


@dataclass
class ServiceUnit:
    name: str
    unit_type: SystemdUnitType
    path: t.Optional[Path] = None
    instanceable: bool = False

    @classmethod
    def parse(cls, raw: Path | str) -> "ServiceUnit":
        path = None
        name: str
        if isinstance(raw, Path):
            name = raw.name
        else:
            name = raw

        if mtch := SERVICE_UNIT_REGEX.match(name):
            return cls(
                name=mtch.groupdict()["name"],
                unit_type=SystemdUnitType(mtch.groupdict()["type"]),
                path=path,
                instanceable="@" in name,
            )
        raise ValueError(f"Unrecognized unit name: '{name}'")


@lru_cache(maxsize=None)
def service_units() -> t.Dict[str, ServiceUnit]:
    """
    Get a dictionary of all recognized systemd service unit types.

    :return: A dictionary mapping unit type names to their corresponding
        :class:`~django_systemd.config.ServiceUnit` instances.
    :rtype: Dict[str, :class:`~django_systemd.config.ServiceUnit`]
    """
    units = {}
    for unit_type in SystemdUnitType:
        unit = ServiceUnit(name="django", unit_type=unit_type)
        units[unit_type.value] = unit
    return units


@lru_cache(maxsize=None)
def template_engine_config() -> t.Dict[str, t.Any]:
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


@lru_cache(maxsize=None)
def render_engine() -> StaticTemplateEngine:
    """
    Get the configured rendering engine for systemd service units.

    :return: Rendering engine that knows how to find and render systemd service unit
        templates.
    :rtype: :class:`~render_static.engine.StaticTemplateEngine`
    """
    return StaticTemplateEngine(template_engine_config())
