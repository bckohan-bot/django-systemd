import pytest

from django_systemd.config import render_engine, template_engine_config


@pytest.fixture(autouse=True)
def clear_config_caches():
    """The engine and its config are cached at module level; isolate every test."""
    render_engine.cache_clear()
    template_engine_config.cache_clear()
    yield
    render_engine.cache_clear()
    template_engine_config.cache_clear()
