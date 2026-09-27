.. include:: ./refs.rst

==========
Change Log
==========

v0.1.0 (unreleased)
===================

* Added ``list``, ``render``, ``install``, ``uninstall``, ``restart`` and
  ``reload`` subcommands to the ``systemd`` management command, all driven by
  the project's unit manifest (:func:`~django_systemd.config.project_units`).
* Added ``render --context`` and ``install --source`` for rendering ahead of
  time and installing pre-rendered units.
* Units are managed in the user scope only; ``sudo`` and system scope support
  have been removed. Nothing runs as root.
* Removed ``SystemdScope`` and ``service_units``.
* :data:`~django_systemd.signals.unit_installed` now sends ``unit`` and
  ``destination``.
* :setting:`SYSTEMD_TEMPLATES` now controls which unit templates are
  discovered.
* Autoescaping is now off by default when rendering unit files, since they are
  not HTML.

v0.1.0 (2026-01-30)
===================

* Initial Release
