"""
All :pypi:`django-systemd` specific :doc:`django:topics/signals` are defined here.
"""

from django.dispatch import Signal

unit_installed = Signal()
"""
Sent by ``django-admin systemd install`` after each unit file has been copied into
the user unit directory, before ``daemon-reload`` runs.

**Signature:**
``(sender, unit, destination, **kwargs)``

:param sender: The running :class:`~django_systemd.management.commands.systemd.Command`
    instance.
:param unit: The :class:`~django_systemd.config.ServiceUnit` that was installed.
:param destination: The :class:`~pathlib.Path` of the installed unit file.
"""
