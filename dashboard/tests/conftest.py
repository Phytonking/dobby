"""Offline dashboard tests. No Postgres, no OAuth providers, no Composio.

Env vars must be set before any dashboard module is imported — database.py and
auth.py read them at import time. The DATABASE_URL points at a closed port; the
engine is created lazily and never connects because get_db is overridden.
"""

import os
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@127.0.0.1:1/test")

import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Fake async DB session (mirrors the SQLAlchemy AsyncSession surface the
# dashboard uses: execute → scalar_one_or_none/scalars/scalar_one, add,
# commit, delete, refresh)
# ---------------------------------------------------------------------------


class FakeResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value

    def scalar_one(self):
        return self._value

    def scalars(self):
        return SimpleNamespace(all=lambda: self._value or [])


class FakeDB:
    """Returns queued results for successive execute() calls."""

    def __init__(self, results=None):
        self.results = list(results or [])
        self.added = []
        self.deleted = []
        self.committed = False

    async def execute(self, stmt, params=None):
        value = self.results.pop(0) if self.results else None
        return FakeResult(value)

    def add(self, obj):
        self.added.append(obj)

    async def delete(self, obj):
        self.deleted.append(obj)

    async def commit(self):
        self.committed = True

    async def refresh(self, obj):
        pass


# ---------------------------------------------------------------------------
# Model stand-ins (plain objects; no Postgres types involved)
# ---------------------------------------------------------------------------


def make_user(role="student", **overrides):
    defaults = dict(
        id=uuid.uuid4(),
        uw_email="student@uw.edu",
        discord_id="123456789",
        display_name="Test Student",
        role=role,
        added_by=None,
        created_at=datetime.now(timezone.utc),
        password_hash=None,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def make_db_session(user, token, expired=False):
    delta = timedelta(days=-1 if expired else 1)
    return SimpleNamespace(
        id=uuid.uuid4(),
        user_id=user.id,
        token=token,
        provider="google",
        expires_at=datetime.now(timezone.utc) + delta,
        created_at=datetime.now(timezone.utc),
    )


# ---------------------------------------------------------------------------
# App fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def app():
    """The FastAPI app with startup seeding stubbed out."""
    from dashboard.main import app as real_app

    with patch("dashboard.main.seed_bootstrap_admin", new=AsyncMock()):
        yield real_app
    real_app.dependency_overrides.clear()


@pytest.fixture()
def client(app):
    from dashboard.database import get_db

    def with_db(fake_db):
        async def _get_db():
            yield fake_db

        app.dependency_overrides[get_db] = _get_db
        return TestClient(app)

    return with_db
