.. include:: ./refs.rst

======
How-To
======

Bundle units with an app
------------------------

Put unit templates in a ``systemd/`` directory inside any installed app. File names
must be ``<name>.<unit type>``, for example ``web.service`` or ``check.timer``.
Templates are Django templates and receive the context described in
:setting:`SYSTEMD_TEMPLATE_CONTEXT`. When two apps provide the same unit name the
app listed first in ``INSTALLED_APPS`` wins. Which templates are discovered is
controlled by the :setting:`SYSTEMD_TEMPLATES` patterns.

Units run in the user manager, so omit ``User=`` and hook into ``default.target``
rather than ``multi-user.target``:

.. code-block:: ini

    [Unit]
    Description={{ settings.PROJECT_NAME|default:"Django" }} web

    [Service]
    ExecStart={{ python }} -m gunicorn --bind unix:%t/web.sock myproject.wsgi
    WorkingDirectory={{ venv }}
    Environment=DJANGO_SETTINGS_MODULE={{ DJANGO_SETTINGS_MODULE }}
    Restart=on-failure

    [Install]
    WantedBy=default.target

.. _user-scope:

Everything runs as the user
----------------------------

All commands use ``systemctl --user`` and install into
``$XDG_CONFIG_HOME/systemd/user`` (``~/.config/systemd/user`` by default). Nothing
in :pypi:`django-systemd` runs as root. Two consequences:

- Talking to the user manager from a non-login session, for example over SSH as a
  deploy user, requires lingering to be enabled once for that user, or
  ``XDG_RUNTIME_DIR`` to be set. Failures show up as ``Failed to connect to bus``
  in the command's error output.

  .. code-block:: bash

      loginctl enable-linger "$USER"

- Enabling lingering also keeps your services running after you log out.

.. note::

    **Developing without systemd**

    ``render`` and ``list`` work on any machine, with or without systemd.
    ``install`` still copies unit files into the user unit directory; if
    ``systemctl`` is not found it skips the rest and prints "systemctl not found;
    skipped daemon-reload and enable." ``uninstall`` behaves the same way,
    printing "systemctl not found; skipped stop, disable and daemon-reload."
    ``restart`` and ``reload`` need systemctl and fail outright with "systemctl
    is not available on this system."

See which units belong to the project
--------------------------------------

.. code-block:: bash

    django-admin systemd list

Each row shows the unit, whether it is installed in the user unit directory,
whether it is enabled and active, and which template it comes from. State columns
show ``-`` for units that are not installed, for template units (``name@.type``),
and when ``systemctl`` is not available.

Render units at package time
----------------------------

Rendering ahead of time makes the unit files reviewable in version control, and a
deploy no longer depends on rendering succeeding on the host. Rendering also bakes
in the interpreter and virtual environment paths of the machine doing the
rendering, so when you render in CI for a different host, override them:

.. code-block:: bash

    django-admin systemd render ./units \
        --context venv=/srv/app/.venv \
        --context python=/srv/app/.venv/bin/python

Commit ``./units`` and install them on the host without rendering again:

.. code-block:: bash

    django-admin systemd install --source ./units --enable

``install`` is still a Django management command even with ``--source``, so
Django settings must still load successfully on the host.

Render and install at deploy time
---------------------------------

On the host, with production settings active:

``--enable`` makes the units start with the user manager at login or boot; it
does not start them now. ``restart`` starts units that are not running and
restarts the ones that are.

.. code-block:: bash

    django-admin systemd install --enable
    django-admin systemd restart

Running ``install`` again replaces the installed unit files, so it doubles as the
update step. The :data:`~django_systemd.signals.unit_installed` signal fires for
each unit as it is copied.

Restart or reload after a deploy
--------------------------------

``restart`` restarts every installed project unit in a single ``systemctl``
transaction, so systemd orders sockets, services and timers itself. ``reload``
reloads services that are running and define ``ExecReload=`` and restarts
everything else; a socket whose service is reloaded in place is left listening.
Both accept an explicit list of unit file names.

Two systemd behaviours to know about:

- systemd refuses to restart a socket on its own while its service is running.
  Restart the pair together (the default, with no names given) or name both.
- The socket-to-service pairing assumes the default ``<name>.service``. A socket
  that sets ``Service=`` to a different unit is restarted like any other unit.

Restart units from a deployment routine
---------------------------------------

With :pypi:`django-routines` you can make unit management part of a deploy routine
without naming the units anywhere in the routine. Add to your settings:

.. code-block:: python

    from django_routines import command, routine

    routine("deploy", "Deploy the site.")
    command("deploy", "migrate")
    command("deploy", "collectstatic", "--noinput")
    command("deploy", "systemd", "install", "--enable")
    command("deploy", "systemd", "reload")

Then ``django-admin routine deploy`` re-installs every project unit (including
any new ones) and reloads or restarts each installed one. Use ``systemd
restart`` instead of ``reload`` when you always want a full restart.

Remove the units
----------------

.. code-block:: bash

    django-admin systemd uninstall

This stops and disables each installed unit, removes its file, and reloads the
daemon. It is safe to run when nothing is installed.
