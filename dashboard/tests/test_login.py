import asyncio
import os
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from starlette.requests import Request

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")
os.environ.setdefault("SECRET_KEY", "dashboard-test-only")

from dashboard import seed
from dashboard.database import Base
from dashboard.models import Session, User
from dashboard.routers import auth


async def database():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")

    @event.listens_for(engine.sync_engine, "connect")
    def sqlite_now(connection, _record):
        connection.create_function("now", 0, lambda: datetime.now(timezone.utc).isoformat())

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


@pytest.mark.parametrize("email", ["person@gmail.com", "person@example.org", "person@uw.edu"])
def test_registered_verified_google_login(email):
    async def run():
        engine, sessions = await database()
        try:
            async with sessions() as db:
                db.add(User(uw_email=email, display_name="Person", role="admin"))
                await db.commit()
                with patch.object(
                    auth.oauth.google,
                    "authorize_access_token",
                    new=AsyncMock(return_value={"userinfo": {"email": email, "email_verified": True}}),
                ):
                    response = await auth.auth_google_callback(Request({"type": "http"}), db)
                assert response.status_code == 307
                assert "dobby_session=" in response.headers["set-cookie"]
            async with sessions() as db:
                assert (await db.execute(select(Session))).scalar_one().provider == "google"
        finally:
            await engine.dispose()

    asyncio.run(run())


@pytest.mark.parametrize(
    "userinfo",
    [
        {"email": "unknown@gmail.com", "email_verified": True},
        {"email": "person@gmail.com", "email_verified": False},
        {"email": "person@gmail.com"},
        {"email_verified": True},
    ],
)
def test_google_rejects_unregistered_or_unverified(userinfo):
    async def run():
        engine, sessions = await database()
        try:
            async with sessions() as db:
                db.add(User(uw_email="person@gmail.com", display_name="Person", role="admin"))
                await db.commit()
                with patch.object(
                    auth.oauth.google,
                    "authorize_access_token",
                    new=AsyncMock(return_value={"userinfo": userinfo}),
                ):
                    with pytest.raises(HTTPException) as error:
                        await auth.auth_google_callback(Request({"type": "http"}), db)
                assert error.value.status_code == 403
                assert (await db.execute(select(Session))).scalar_one_or_none() is None
        finally:
            await engine.dispose()

    asyncio.run(run())


@pytest.mark.parametrize("existing_role", [None, "student", "admin"])
def test_bootstrap_admin(existing_role, monkeypatch):
    monkeypatch.setenv("BOOTSTRAP_ADMIN_EMAIL", "person@gmail.com")

    async def run():
        engine, sessions = await database()
        try:
            if existing_role:
                async with sessions() as db:
                    db.add(User(uw_email="person@gmail.com", display_name="Person", role=existing_role))
                    await db.commit()
            with patch.object(seed, "SessionLocal", sessions):
                await seed.seed_bootstrap_admin()
                await seed.seed_bootstrap_admin()
                monkeypatch.setenv("BOOTSTRAP_ADMIN_EMAIL", "other@gmail.com")
                await seed.seed_bootstrap_admin()
            async with sessions() as db:
                user = (await db.execute(select(User))).scalar_one()
                assert user.uw_email == "person@gmail.com"
                assert user.role == "admin"
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_google_redirect_has_no_domain_hint():
    async def run():
        request = AsyncMock()
        request.url_for = lambda name: "http://localhost:8000/auth/google/callback"
        with patch.object(auth.oauth.google, "authorize_redirect", new=AsyncMock()) as redirect:
            await auth.auth_google(request)
            assert "hd" not in redirect.call_args.kwargs
        assert "hd" not in auth.oauth.google.client_kwargs

    asyncio.run(run())
