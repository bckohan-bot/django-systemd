.. include:: ./refs.rst

========
Settings
========

``SYSTEMD_TEMPLATE_ENGINE``
---------------------------

.. setting:: SYSTEMD_TEMPLATE_ENGINE

The :setting:`SYSTEMD_TEMPLATE_ENGINE` setting defines the configuration for the
template engine used to render systemd unit files. It follows the same structure as the
:pypi:`django-render-static` setting :setting:`STATIC_TEMPLATES` (itself a superset of the Django_
setting :setting:`TEMPLATES`) but provides defaults tailored for systemd unit file rendering.

By default it will load templates from app directories named ``systemd``. It follows the
same precedence rules as Django's template engine configuration - with higher precedence
apps overriding templates of the same name in lower precedence apps.

By default it will find templates that have recognized systemd unit file extensions.

A number of convenient environment context variables are added to the context. See
:setting:`SYSTEMD_TEMPLATE_CONTEXT` for the list.

.. tip::

    If you want to tweak the recognized template names or add additional context variables, instead
    of overriding the entire setting, consider using the :setting:`SYSTEMD_TEMPLATES` and
    :setting:`SYSTEMD_TEMPLATE_CONTEXT` settings

Default:

.. code-block:: python

    {
        "ENGINES": [{
            "BACKEND": "render_static.backends.StaticDjangoTemplates",
            "OPTIONS": {
                "app_dir": "systemd",
                "builtins": [
                    "render_static.templatetags.render_static"
                ],
                "loaders": [
                    "render_static.loaders.StaticAppDirectoriesBatchLoader"
                ]
            }
        }],
        "context": {
            "DJANGO_SETTINGS_MODULE": "...",
            "django-admin": "<manage script name or path if not on PATH>",
            "python": "<python interpreter path>",
            "settings": "<django.conf.settings>",
            "venv": "<virtual environment path>"},
        "templates": [
            "**/*.service", "**/*.socket", "**/*.target", "**/*.timer", "**/*.path",
            "**/*.mount", "**/*.automount", "**/*.swap", "**/*.device", "**/*.scope",
            "**/*.snapshot", "**/*.slice"
        ]
    }


``SYSTEMD_TEMPLATES``
---------------------

.. setting:: SYSTEMD_TEMPLATES

The :setting:`SYSTEMD_TEMPLATES` setting defines a list of the :func:`~glob.glob` patterns used to
identify systemd unit file templates within the template engine. By default it will recognize any
file with a standard systemd unit file extension. The :func:`~glob.glob` patterns are ``recursive``.

Default:

    .. code-block:: python

        [
            "**/*.service", "**/*.socket", "**/*.target", "**/*.timer", "**/*.path",
            "**/*.mount", "**/*.automount", "**/*.swap", "**/*.device", "**/*.scope",
            "**/*.snapshot", "**/*.slice"
        ]


``SYSTEMD_TEMPLATE_CONTEXT``
----------------------------

.. setting:: SYSTEMD_TEMPLATE_CONTEXT

Add additional context variables to the rendering context. Provided contexts do not need to be
dictionaries, but can be provided from :ref:`multiple sources <django-render-static:context>`.

**These values will always be added to the context even if you provide your own context.**

- ``DJANGO_SETTINGS_MODULE``: The value of the :envvar:`DJANGO_SETTINGS_MODULE` environment
  variable.
- ``django-admin``: The name or path of the Django
  :doc:`management script <django:ref/django-admin>`.
- ``python``: The path to the Python :py:data:`interpreter <python:sys.executable>`.
- ``settings``: The Django :doc:`settings module <django:ref/settings>`.
- ``venv``: The path to the active python :py:data:`environment <python:sys.prefix>`.
