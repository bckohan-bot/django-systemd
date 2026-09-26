# django-systemd

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](https://opensource.org/licenses/MIT)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
[![PyPI version](https://badge.fury.io/py/django-systemd.svg)](https://pypi.python.org/pypi/django-systemd/)
[![PyPI pyversions](https://img.shields.io/pypi/pyversions/django-systemd.svg)](https://pypi.python.org/pypi/django-systemd/)
[![PyPI djversions](https://img.shields.io/pypi/djversions/django-systemd.svg)](https://pypi.org/project/django-systemd/)
[![PyPI status](https://img.shields.io/pypi/status/django-systemd.svg)](https://pypi.python.org/pypi/django-systemd)
[![Documentation Status](https://readthedocs.org/projects/django-systemd/badge/?version=latest)](http://django-systemd.readthedocs.io/?badge=latest/)
[![Code Cov](https://codecov.io/gh/bckohan/django-systemd/branch/main/graph/badge.svg)](https://codecov.io/gh/bckohan/django-systemd)
[![Test Status](https://github.com/bckohan/django-systemd/actions/workflows/test.yml/badge.svg?branch=main)](https://github.com/bckohan/django-systemd/actions/workflows/test.yml?query=branch:main)
[![Lint Status](https://github.com/bckohan/django-systemd/actions/workflows/lint.yml/badge.svg?branch=main)](https://github.com/bckohan/django-systemd/actions/workflows/lint.yml?query=branch:main)
[![OpenSSF Scorecard](https://api.securityscorecards.dev/projects/github.com/bckohan/django-systemd/badge)](https://securityscorecards.dev/viewer/?uri=github.com/bckohan/django-systemd)

`django-systemd` does two independent things for a Django deployment:

1. **Generate systemd unit files.** Apps bundle unit templates in a `systemd/`
   directory. Render them at package time with known settings and commit the
   result, or render them at deploy time from live production settings.
2. **Manage the project's units.** `django-admin systemd` can list, install,
   update, restart and reload every unit the project defines, without you
   naming them. Everything runs in the user scope via `systemctl --user`; nothing
   runs as root.

```bash
django-admin systemd list
django-admin systemd render ./units --context venv=/srv/app/.venv  # in CI
django-admin systemd install --enable  # on the host
django-admin systemd reload  # on the host
```

See the [documentation](https://django-systemd.readthedocs.io) for the how-to and settings.
