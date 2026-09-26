.. include:: ./refs.rst

======
How-To
======

Bundle units with an app
------------------------

Put unit templates in a ``systemd/`` directory inside any installed app. File names
must be ``<name>.<unit type>``, for example ``web.service`` or ``check.timer``.
Templates are Django templates and receive the context described in
:ref:`settings`. When two apps provide the same unit name the app listed first in
``INSTALLED_APPS`` wins. Which templates are discovered is controlled by the
:ref:`SYSTEMD_TEMPLATES <settings>` patterns.

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

Rendering bakes in the interpreter and virtual environment paths of the machine
doing the rendering. When you render in CI for a different host, override them:

.. code-block:: bash

    django-admin systemd render ./units \
        --context venv=/srv/app/.venv \
        --context python=/srv/app/.venv/bin/python

Commit ``./units`` and install them on the host without rendering again:

.. code-block:: bash

    django-admin systemd install --source ./units --enable

Render and install at deploy time
---------------------------------

On the host, with production settings active:

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

Then ``django-admin routine deploy`` re-installs every project unit, picking up
anything new or changed, and reloads (or restarts) each one. Use
``systemd restart`` instead of ``reload`` when you always want a full restart.

Remove the units
----------------

.. code-block:: bash

    django-admin systemd uninstall

This stops and disables each installed unit, removes its file, and reloads the
daemon. It is safe to run when nothing is installed.

Everything runs as the user
---------------------------

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
