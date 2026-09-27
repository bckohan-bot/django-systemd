.. include:: ./refs.rst

==========
Change Log
==========

v0.1.0 (unreleased)
===================

* Initial Release.
* Requires Python 3.11 or later and Django 5.2 or later.
* Apps bundle systemd unit templates in a ``systemd/`` directory. The set of
  templates matching :setting:`SYSTEMD_TEMPLATES` forms the project's unit
  manifest (:func:`~django_systemd.config.project_units`).
* The ``systemd`` management command provides ``list``, ``render``,
  ``install``, ``uninstall``, ``restart`` and ``reload``, all driven by the
  manifest so deployments never name units.
* ``render --context`` and ``install --source`` support rendering at package
  time and installing the pre-rendered units on the host.
* Units are managed in the user scope only, through ``systemctl --user``.
  Nothing runs as root.
* :data:`~django_systemd.signals.unit_installed` is sent with ``unit`` and
  ``destination`` for each installed unit.
* Unit files are rendered with autoescaping off, since they are not HTML.
