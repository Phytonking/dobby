"""Real Postgres and real ASGI middleware; only provider calls are substituted."""

import os
from datetime import datetime, timedelta, timezone

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from dashboard.auth import COOKIE_NAME, make_token
from dashboard.database import get_db
from dashboard.main import app
from dashboard.models import Session, User
from dashboard.passwords import hash_password
from dashboard.routers import auth

PASSWORD = "regression-password-only"


@pytest.fixture(autouse=True)
def isolated_settings(monkeypatch):
    if os.environ.get("DOBBY_REGRESSION") != "1":
        pytest.fail("Run this suite with python test_regression/run.py (isolated test database required).")
    monkeypatch.setenv("AUTH_MODE", "local")
    monkeypatch.setenv("LOCAL_NETWORKS", "127.0.0.0/8,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16")
    auth._attempts.clear()


@pytest_asyncio.fixture
async def db():
    engine = create_async_engine(os.environ["DATABASE_URL"])
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.execute(
            text("TRUNCATE users, sessions, integrations, agent_actions RESTART IDENTITY CASCADE")
        )
    async with sessions() as session:
        admin = User(
            display_name="Admin",
            role="admin",
            local_username="admin",
            password_hash=hash_password(PASSWORD),
            discord_id="1",
            uw_email="admin@example.org",
        )
        member = User(
            display_name="Member",
            role="student",
            local_username="member",
            password_hash=hash_password(PASSWORD),
            discord_id="2",
            uw_email="member@example.org",
        )
        session.add_all([admin, member])
        await session.flush()
        tokens = {}
        for user in (admin, member):
            token = make_token(str(user.id))
            tokens[user.role] = token
            session.add(
                Session(
                    user_id=user.id,
                    token=token,
                    provider="local",
                    expires_at=datetime.now(timezone.utc) + timedelta(days=1),
                )
            )
        await session.commit()

    async def override():
        async with sessions() as session:
            yield session

    app.dependency_overrides[get_db] = override
    yield sessions, admin, member, tokens
    app.dependency_overrides.clear()
    await engine.dispose()


@pytest_asyncio.fixture
async def clients(db):
    transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 1234), raise_app_exceptions=False)
    async with (
        httpx.AsyncClient(transport=transport, base_url="http://dashboard") as admin,
        httpx.AsyncClient(transport=transport, base_url="http://dashboard") as member,
        httpx.AsyncClient(transport=transport, base_url="http://dashboard") as anonymous,
    ):
        admin.cookies.set(COOKIE_NAME, db[3]["admin"])
        member.cookies.set(COOKIE_NAME, db[3]["student"])
        yield admin, member, anonymous
