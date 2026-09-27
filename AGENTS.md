# AGENTS.md

This file is for Claude Code and other AI coding assistants working in this repository.

## What This Repo Is

**django-systemd** — Is a django app that makes it easy to do two things - neither of which depend on each other.

1. Generate systemd files. These files can be generated
  1. At package time and committed to CI with known settings
  2. At deploy time from live production settings
2. Manage systemd units - installing/updating/listing them. We assume that systemd units are all meant to be run as the user not as root.

A Django application library. Source lives in `src/django_systemd/`. Tests are in `tests/`. Documentation is in `doc/`.

## Tooling

Uses `just` as a task runner, `uv` for dependency management, and `hatchling` as the build backend.

### Setup
```bash
just setup        # create .venv + install prek pre-commit hooks
just install      # sync all dev dependencies
```

### Tests
```bash
just test                              # run tests against project venv (fast iteration)
just test tests/test_foo.py            # run a specific file
just test tests/test_foo.py::TestClass::test_method   # run a single test
just test-all --group dj52             # run full isolated suite against Django 5.2
just coverage                          # combine and report coverage
```

`just test` uses the project venv with `--no-sync` for speed. `just test-all` runs in a fully isolated environment and accepts any `uv run` flags (e.g. `-p 3.12 --group dj61 --resolution lowest-direct`).

### Linting / Formatting
```bash
just fix          # auto-fix lint + format
just check        # all static checks without modifying files
just check-all    # all checks including doc link checking (slow)
just prek         # run pre-commit hooks
```

### Type Checking
```bash
just check-types            # mypy + pyright (project venv)
just check-types-isolated   # mypy + pyright in isolated env
```

### Docs
```bash
just docs         # build Sphinx HTML and open in browser
just docs-live    # live-reload dev server
just check-docs   # lint docs with doc8
```

### Django Management
```bash
just manage <command>   # run django-admin commands (uses tests.settings)
```

### Release
```bash
just release 1.2.3   # validates version, tags, and pushes tag to GitHub
```

## Test Strategy

Tests use `pytest` with `pytest-django`. Test settings are in `tests/settings.py` (`DJANGO_SETTINGS_MODULE=tests.settings`). Test apps in `tests/apps/app1/` and `tests/apps/app2/` contain example systemd unit templates for testing template precedence and rendering.

Django versions are selected at test-run time via mutually exclusive `uv` dependency groups: `dj52`, `dj61`. CI passes these as `--group` flags to `just test-all`, and also runs the lowest supported direct dependency versions on the oldest supported Python with `--resolution lowest-direct`.

## Architecture

**Core components**:
- `defines.py`: Dataclass enums (`SystemdEnum`) for systemd unit types (`SystemdUnitType`), startup types and restart types; each member's `value` is the literal systemd string and it carries a `description`
- `config.py`: Template engine configuration using `django-render-static` to discover and render systemd unit templates from app `systemd/` directories
- `management/commands/systemd.py`: Django management command built with `django-typer`

**Key dependencies**:
- `django-render-static`: Template discovery and rendering engine for finding systemd units in app directories
- `django-typer`: CLI framework for the `systemd` management command

**Template system**: Apps can bundle systemd unit templates in their `systemd/` subdirectory. The engine uses `StaticAppDirectoriesBatchLoader` to find templates matching `**/*.{service,socket,timer,...}` patterns. Templates have access to `settings`, `venv`, `python`, `django-admin`, and `DJANGO_SETTINGS_MODULE` in their context.

**Configuration settings**:
- `SYSTEMD_TEMPLATE_ENGINE`: Override the template engine configuration
- `SYSTEMD_TEMPLATE_CONTEXT`: Add custom context variables for templates
- `SYSTEMD_TEMPLATES`: Override which template patterns to discover

## Project Structure

```
src/django_systemd/   # library source
tests/
  settings.py         # Django test settings
  apps/               # test apps bundling example systemd unit templates
doc/source/           # Sphinx documentation source
```
