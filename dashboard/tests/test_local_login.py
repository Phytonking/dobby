import asyncio
from unittest.mock import patch

import httpx
import pytest
from fastapi import Depends, FastAPI, HTTPException
from sqlalchemy import select
from starlette.requests import Request

from .test_login import database
from dashboard import local_admin
from dashboard.auth import get_current_user
from dashboard.database import get_db
from dashboard.login_config import (
    dashboard_url,
    dashboard_urls,
    is_local_request,
    require_provider,
)
from dashboard.models import Session, User
from dashboard.passwords import hash_password, verify_password
from dashboard.routers import auth


def test_password_hashing():
    password = "a-long-local-password"
    first = hash_password(password)
    assert first != hash_password(password)
    assert password not in first
    assert verify_password(password, first)
    assert not verify_password("incorrect-password", first)
    assert not verify_password(password, "invalid")
    with pytest.raises(ValueError):
        hash_password("short")


@pytest.mark.parametrize(
    "host, allowed",
    [
        ("192.168.1.50", True),
        ("10.0.0.5", True),
        ("172.16.0.5", True),
        ("127.0.0.1", True),
        ("::1", True),
        ("fd00::1", True),
        ("::ffff:192.168.1.50", True),
        ("8.8.8.8", False),
        ("172.32.0.1", False),
        ("0.0.0.0", False),
        ("testclient", False),
        # Tailscale IPv4 (RFC 6598 CGNAT) is not private, so it is not local until opted in
        # via LOCAL_NETWORKS. Its IPv6 prefix does sit inside fc00::/7.
        ("100.64.0.1", False),
        ("100.101.102.103", False),
        ("fd7a:115c:a1e0::1", True),
    ],
)
def test_local_networks(host, allowed):
    request = Request({"type": "http", "client": (host, 1234), "headers": []})
    assert is_local_request(request) is allowed


def test_local_networks_replaces_the_defaults(monkeypatch):
    """LOCAL_NETWORKS is the whole definition of "local", so it can widen or narrow."""
    tailscale = Request({"type": "http", "client": ("100.101.102.103", 1234), "headers": []})
    lan = Request({"type": "http", "client": ("192.168.1.50", 1234), "headers": []})

    # Widened to admit a tailnet, keeping the private ranges.
    monkeypatch.setenv("LOCAL_NETWORKS", "192.168.0.0/16, 100.64.0.0/10")
    assert is_local_request(tailscale) is True
    assert is_local_request(lan) is True

    # Narrowed to one subnet.
    monkeypatch.setenv("LOCAL_NETWORKS", "192.168.0.0/16")
    assert is_local_request(tailscale) is False
    assert is_local_request(lan) is True


def test_refused_client_address_is_logged(monkeypatch, caplog):
    """The refusal must name the address, so LOCAL_NETWORKS can be set from evidence."""
    monkeypatch.setenv("AUTH_MODE", "local")
    monkeypatch.delenv("LOCAL_NETWORKS", raising=False)
    request = Request({"type": "http", "client": ("100.101.102.103", 1234), "headers": []})
    with caplog.at_level("WARNING"):
        with pytest.raises(HTTPException) as error:
            require_provider("local", request)
    assert error.value.status_code == 403
    assert "100.101.102.103" in caplog.text
    assert "LOCAL_NETWORKS" in caplog.text


def test_invalid_local_networks_fails_loudly(monkeypatch):
    monkeypatch.setenv("LOCAL_NETWORKS", "not-a-cidr")
    request = Request({"type": "http", "client": ("192.168.1.50", 1234), "headers": []})
    with pytest.raises(RuntimeError, match="LOCAL_NETWORKS"):
        is_local_request(request)


def test_dashboard_urls_accepts_several_origins(monkeypatch):
    monkeypatch.delenv("DASHBOARD_URL", raising=False)
    assert dashboard_urls() == ["http://localhost:3000"]
    monkeypatch.setenv("DASHBOARD_URL", "http://100.101.102.103:3000/, http://localhost:3000")
    assert dashboard_urls() == ["http://100.101.102.103:3000", "http://localhost:3000"]
    # The first entry stays the canonical post-login redirect target.
    assert dashboard_url() == "http://100.101.102.103:3000"


@pytest.mark.parametrize(
    "mode, provider, allowed",
    [
        ("oauth", "google", True),
        ("oauth", "discord", True),
        ("oauth", "local", False),
        ("local", "google", False),
        ("local", "discord", False),
        ("local", "local", True),
        ("both", "google", True),
        ("both", "local", True),
    ],
)
def test_modes(mode, provider, allowed, monkeypatch):
    monkeypatch.setenv("AUTH_MODE", mode)
    request = Request({"type": "http", "client": ("192.168.1.50", 1234), "headers": []})
    if allowed:
        require_provider(provider, request)
    else:
        with pytest.raises(HTTPException) as error:
            require_provider(provider, request)
        assert error.value.status_code == 404


def test_local_login_session_logout_and_reset(monkeypatch):
    monkeypatch.setenv("AUTH_MODE", "both")
    auth._attempts.clear()

    async def run():
        engine, sessions = await database()
        app = FastAPI()
        app.include_router(auth.router, prefix="/auth")

        async def db_override():
            async with sessions() as db:
                yield db

        app.dependency_overrides[get_db] = db_override

        @app.get("/me")
        async def me(user=Depends(get_current_user)):
            return {"role": user.role}

        try:
            with patch.object(local_admin, "SessionLocal", sessions):
                await local_admin.save_admin("leona", hash_password("a-long-local-password"))
            transport = httpx.ASGITransport(app=app, client=("192.168.1.50", 1234))
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                credentials = {"username": " Leona ", "password": "a-long-local-password"}
                assert (await client.get("/auth/methods")).json() == {"local": True, "oauth": True}
                assert (await client.get("/me")).status_code == 401
                assert (
                    await client.post(
                        "/auth/local", json=credentials, headers={"Origin": "http://evil.example"}
                    )
                ).status_code == 403
                response = await client.post("/auth/local", json=credentials)
                assert response.status_code == 200
                assert "HttpOnly" in response.headers["set-cookie"]
                assert (await client.get("/me")).json() == {"role": "admin"}
                monkeypatch.setenv("AUTH_MODE", "oauth")
                assert (await client.get("/me")).status_code == 404
                assert (await client.post("/auth/local", json=credentials)).status_code == 404
                monkeypatch.setenv("AUTH_MODE", "both")
                assert (await client.post("/auth/logout")).status_code == 204
                assert (await client.get("/me")).status_code == 401
                assert (await client.post("/auth/local", json=credentials)).status_code == 200
                with patch.object(local_admin, "SessionLocal", sessions):
                    await local_admin.save_admin("leona", hash_password("changed-local-password"))
                assert (await client.get("/me")).status_code == 401
                assert (await client.post("/auth/local", json=credentials)).status_code == 401
                credentials["password"] = "changed-local-password"
                assert (await client.post("/auth/local", json=credentials)).status_code == 200
                assert (await client.post("/auth/local", json=credentials)).status_code == 200
                assert (await client.post("/auth/local", json=credentials)).status_code == 429
            public = httpx.ASGITransport(app=app, client=("8.8.8.8", 1234))
            async with httpx.AsyncClient(transport=public, base_url="http://test") as client:
                assert (await client.post("/auth/local", json=credentials)).status_code == 403
            async with sessions() as db:
                assert len((await db.execute(select(User))).scalars().all()) == 1
                assert all(s.provider == "local" for s in (await db.execute(select(Session))).scalars())
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_tailscale_client_can_log_in_end_to_end(monkeypatch):
    """With its range in LOCAL_NETWORKS, a tailnet peer must clear all three gates: main.py's
    local-network middleware, require_provider, and the login origin check."""
    monkeypatch.setenv("AUTH_MODE", "local")
    monkeypatch.setenv("LOCAL_NETWORKS", "192.168.0.0/16, 100.64.0.0/10")
    monkeypatch.setenv("DASHBOARD_URL", "http://100.101.102.103:3000,http://localhost:3000")
    auth._attempts.clear()

    from dashboard.main import app  # the real middleware stack, not a rebuilt one

    async def run():
        engine, sessions = await database()

        async def db_override():
            async with sessions() as db:
                yield db

        app.dependency_overrides[get_db] = db_override
        try:
            with patch.object(local_admin, "SessionLocal", sessions):
                await local_admin.save_admin("leona", hash_password("a-long-local-password"))
            credentials = {"username": "leona", "password": "a-long-local-password"}
            tailscale = httpx.ASGITransport(app=app, client=("100.101.102.103", 1234))
            async with httpx.AsyncClient(transport=tailscale, base_url="http://test") as client:
                origin = {"Origin": "http://100.101.102.103:3000"}
                assert (await client.post("/auth/local", json=credentials, headers=origin)).status_code == 200
                assert (await client.get("/me")).status_code == 200
                # A stale localhost bundle is still a configured origin.
                assert (
                    await client.post(
                        "/auth/local", json=credentials, headers={"Origin": "http://localhost:3000"}
                    )
                ).status_code == 200
                assert (
                    await client.post(
                        "/auth/local", json=credentials, headers={"Origin": "http://evil.example"}
                    )
                ).status_code == 403
            public = httpx.ASGITransport(app=app, client=("8.8.8.8", 1234))
            async with httpx.AsyncClient(transport=public, base_url="http://test") as client:
                response = await client.post("/auth/local", json=credentials)
                assert response.status_code == 403
                assert response.json()["detail"] == "Local network access only"
        finally:
            app.dependency_overrides.clear()
            await engine.dispose()

    asyncio.run(run())


def test_password_prompt_retries_without_changing_password(capsys):
    password = "  a-long-password  "
    with patch.object(
        local_admin,
        "getpass",
        side_effect=[
            "first-long-password",
            "different-password",
            password,
            password,
        ],
    ) as prompt:
        assert local_admin.prompt_password() == password
        assert prompt.call_count == 4
    output = capsys.readouterr().err
    assert "Nothing was saved" in output
    assert password not in output


def test_password_prompt_no_confirm():
    with patch.object(local_admin, "getpass", return_value="a-long-password") as prompt:
        assert local_admin.prompt_password(confirm=False) == "a-long-password"
        prompt.assert_called_once()


def test_password_prompt_no_confirm_still_checks_length():
    with patch.object(local_admin, "getpass", side_effect=["short", "a-long-password"]) as prompt:
        assert local_admin.prompt_password(confirm=False) == "a-long-password"
        assert prompt.call_count == 2


def test_password_prompt_stops_after_three_mismatches():
    with patch.object(local_admin, "getpass", side_effect=["a-long-password", "different-password"] * 3):
        with pytest.raises(ValueError, match="nothing was saved"):
            local_admin.prompt_password()
