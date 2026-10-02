"""Tests that hit real external services — currently just Composio.

Not a mock, not a stub: this is the one place that calls the real Composio
API with the real COMPOSIO_API_KEY and asserts on what it actually returns.
It exists because the gap it closes is real — chatting with the live bot hit
two bugs (975-tool request, null `strict` field) that neither the mocked
unit suite nor the eval harness could ever have caught, since eval
deliberately stubs Composio to avoid real side effects on the live calendar.

Skipped entirely unless COMPOSIO_API_KEY is a real shell env var — deliberately
not auto-loaded from .env here, same convention as tests/integration's
TEST_DATABASE_URL gate. Without that, a plain `pytest -q` / `make test` on any
machine with a working .env would silently start making real network calls on
every run, which is exactly the "needs no live credentials" guarantee the rest
of the suite promises. No docker, no postgres — just network and a real key:

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
