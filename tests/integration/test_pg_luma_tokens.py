"""Dobby's Luma tokens against real Postgres: read, refresh before expiry, rotate, and fail clearly."""

import asyncio
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs

import httpx
import pytest
import sqlalchemy as sa

from .conftest import TEST_DATABASE_URL


def _store(token_handler):
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from bot.integrations.luma.mcp import TokenStore

    engine = create_async_engine(TEST_DATABASE_URL)
    store = TokenStore(
        session_factory=async_sessionmaker(engine, expire_on_commit=False),
        http_factory=lambda: httpx.AsyncClient(transport=httpx.MockTransport(token_handler)),
    )
    return engine, store


async def _put(engine, *, expires_at, refresh_token="rt-1"):
    async with engine.begin() as conn:
        await conn.execute(sa.text("DELETE FROM luma_connection"))
        await conn.execute(
            sa.text(
                "INSERT INTO luma_connection (id, client_id, token_endpoint, resource, access_token, "
                "refresh_token, expires_at) VALUES (1, 'https://dobby.example/client.json', "
                "'https://api.luma.test/oauth/token', 'https://mcp.luma.com', 'at-1', :r, :e)"
            ),
            {"r": refresh_token, "e": expires_at},
        )


async def _row(engine):
    async with engine.connect() as conn:
        return (await conn.execute(sa.text("SELECT * FROM luma_connection"))).mappings().first()


def test_fresh_token_is_used_as_is(migrated_db):
    calls = []

    async def scenario():
        engine, store = _store(lambda r: calls.append(r) or httpx.Response(500))
        try:
            await _put(engine, expires_at=datetime.now(timezone.utc) + timedelta(hours=1))
            assert await store.access_token() == "at-1"
        finally:
            await engine.dispose()

    asyncio.run(scenario())
    assert calls == []


def test_expiring_token_is_refreshed_and_the_rotated_pair_saved(migrated_db):
    sent = []

    def handler(request):
        sent.append({k: v[0] for k, v in parse_qs(request.content.decode()).items()})
        return httpx.Response(200, json={"access_token": "at-2", "refresh_token": "rt-2", "expires_in": 3600})

    async def scenario():
        engine, store = _store(handler)
        try:
            await _put(engine, expires_at=datetime.now(timezone.utc) + timedelta(seconds=30))
            assert await store.access_token() == "at-2"
            row = await _row(engine)
            assert (row["access_token"], row["refresh_token"]) == ("at-2", "rt-2")
            assert row["expires_at"] > datetime.now(timezone.utc) + timedelta(minutes=55)
            # The next call uses the stored token without refreshing again.
            assert await store.access_token() == "at-2"
        finally:
            await engine.dispose()

    asyncio.run(scenario())
    assert sent == [
        {
            "grant_type": "refresh_token",
            "refresh_token": "rt-1",
            "client_id": "https://dobby.example/client.json",
            "resource": "https://mcp.luma.com",
        }
    ]


def test_concurrent_refreshes_use_the_refresh_token_once(migrated_db):
    """Luma rotates refresh tokens; two racing refreshes would burn the second one."""
    used = []

    def handler(request):
        token = parse_qs(request.content.decode())["refresh_token"][0]
        used.append(token)
        n = len(used) + 1
        return httpx.Response(
            200, json={"access_token": f"at-{n}", "refresh_token": f"rt-{n}", "expires_in": 3600}
        )

    async def scenario():
        engine, store = _store(handler)
        try:
            await _put(engine, expires_at=datetime.now(timezone.utc) - timedelta(minutes=1))
            tokens = await asyncio.gather(*(store.access_token() for _ in range(3)))
            assert set(tokens) == {"at-2"}
        finally:
            await engine.dispose()

    asyncio.run(scenario())
    assert used == ["rt-1"]


def test_rejected_refresh_asks_for_a_reconnect(migrated_db):
    from bot.integrations.luma.mcp import RECONNECT
    from bot.models import UserError

    async def scenario():
        engine, store = _store(lambda r: httpx.Response(400, json={"error": "invalid_grant"}))
        try:
            await _put(engine, expires_at=datetime.now(timezone.utc) - timedelta(minutes=1))
            with pytest.raises(UserError, match="reconnect Luma"):
                await store.access_token()
            assert (await _row(engine))["access_token"] == "at-1"  # nothing overwritten
        finally:
            await engine.dispose()

    asyncio.run(scenario())
    assert "reconnect" in RECONNECT


def test_not_connected_says_how_to_connect(migrated_db):
    from bot.models import UserError

    async def scenario():
        engine, store = _store(lambda r: httpx.Response(500))
        try:
            async with engine.begin() as conn:
                await conn.execute(sa.text("DELETE FROM luma_connection"))
            with pytest.raises(UserError, match="connect it on the dashboard"):
                await store.access_token()
        finally:
            await engine.dispose()

    asyncio.run(scenario())
