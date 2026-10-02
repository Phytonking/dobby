"""Tests that hit real external services — currently just Composio.

The one place that calls the real Composio API with the real COMPOSIO_API_KEY and asserts on what
it returns: mocks and the eval harness (which stubs Composio to avoid side effects) cannot see a
renamed action or a stale toolkit version pin.

Skipped entirely unless COMPOSIO_API_KEY is a real shell env var — deliberately not auto-loaded
from .env, same convention as tests/integration's TEST_DATABASE_URL gate, so a plain `pytest -q`
never makes network calls. No docker, no postgres — just network and a real key:

    make test-composio             # loads .env for you via `dotenv run`
    COMPOSIO_API_KEY=... pytest tests/live -q
"""

import os
from pathlib import Path

import pytest

COMPOSIO_API_KEY = os.environ.get("COMPOSIO_API_KEY", "")


def pytest_collection_modifyitems(config, items):
    if COMPOSIO_API_KEY:
        return
    here = Path(__file__).parent
    skip = pytest.mark.skip(reason="needs COMPOSIO_API_KEY (env or .env) — run `pytest tests/live`")
    for item in items:
        if here in Path(str(item.fspath)).parents:
            item.add_marker(skip)
