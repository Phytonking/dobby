"""Postgres-backed integration tests.

Run against a real database (migrations, raw SQL casts, JSONB/ARRAY/UUID
round-trips) — everything the mocked unit suite cannot see.

Skipped entirely unless TEST_DATABASE_URL is set. Locally:

    make test-integration          # spins up a throwaway postgres via docker

or point TEST_DATABASE_URL at any scratch postgres:

    TEST_DATABASE_URL=postgresql+asyncpg://dobby:dobby@127.0.0.1:5433/dobby_test \
        pytest tests/integration
"""

import asyncio
import os
from pathlib import Path

import pytest

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "")
REPO_ROOT = Path(__file__).resolve().parents[2]

# dashboard modules read these at import time; tests never use the real values.
os.environ.setdefault("SECRET_KEY", "integration-test-secret")
os.environ.setdefault("DATABASE_URL", TEST_DATABASE_URL or "postgresql+asyncpg://x:x@127.0.0.1:1/x")


def pytest_collection_modifyitems(config, items):
    if TEST_DATABASE_URL:
        return
    here = Path(__file__).parent
    skip = pytest.mark.skip(reason="needs TEST_DATABASE_URL — run `make test-integration`")
    for item in items:
        # This hook sees the whole session's items; only skip ours.
        if here in Path(str(item.fspath)).parents:
            item.add_marker(skip)


@pytest.fixture(scope="session")
def migrated_db():
    """Apply alembic migrations (upgrade head) once for the whole session."""
    from alembic import command
    from alembic.config import Config

    os.environ["DATABASE_URL"] = TEST_DATABASE_URL  # read by migrations/env.py
    cfg = Config(str(REPO_ROOT / "migrations" / "alembic.ini"))
    cfg.set_main_option("script_location", str(REPO_ROOT / "migrations"))
    command.upgrade(cfg, "head")
    return TEST_DATABASE_URL


def run_db(fn):
    """Run an async fn(session) against the test database on a fresh engine."""
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    async def wrapper():
        engine = create_async_engine(TEST_DATABASE_URL)
        try:
            session_factory = async_sessionmaker(engine, expire_on_commit=False)
            async with session_factory() as session:
                await fn(session)
        finally:
            await engine.dispose()

    asyncio.run(wrapper())
