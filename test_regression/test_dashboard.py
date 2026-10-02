import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from sqlalchemy import select, text

from bot.memory import find_user_by_discord_id, record_action, set_calendar_email
from dashboard import local_admin
from dashboard.auth import COOKIE_NAME, set_session_cookie
from dashboard.models import Session
from dashboard.passwords import hash_password
from dashboard.routers import auth, integrations
from starlette.responses import Response

pytestmark = pytest.mark.asyncio


async def test_04_dashboard_without_connected_accounts(clients):
    admin, member, anonymous = clients
    assert (await anonymous.get("/health")).json() == {"ok": True}
    assert (await admin.get("/integrations")).json() == []
    assert (await admin.get("/admin/users")).json()["total"] == 2
    result = await member.patch("/me", json={"calendar_email": " Member@Example.org "})
    assert result.status_code == 200, result.text
    assert (await member.get("/me")).json()["calendar_email"] == "member@example.org"


@pytest.mark.parametrize(
    "mode,local,oauth", [("local", True, False), ("oauth", False, True), ("both", True, True)]
)
async def test_11_dashboard_login_modes(clients, monkeypatch, mode, local, oauth):
    monkeypatch.setenv("AUTH_MODE", mode)
    admin, _, anonymous = clients
    assert (await anonymous.get("/auth/methods")).json() == {"local": local, "oauth": oauth}
    response = await anonymous.post(
        "/auth/local", json={"username": "admin", "password": "regression-password-only"}
    )
    assert response.status_code == (200 if local else 404)
    assert (await admin.get("/me")).status_code == (200 if local else 404)
    if not oauth:
        assert (await anonymous.get("/auth/google")).status_code == 404
        assert (await anonymous.get("/auth/discord")).status_code == 404


async def test_12_dashboard_session_lifecycle(clients, db, monkeypatch):
    admin, member, anonymous = clients
    token = db[3]["admin"]
    anonymous.cookies.set(COOKIE_NAME, token + "tampered")
    assert (await anonymous.get("/me")).status_code == 401
    assert (await admin.post("/auth/logout")).status_code == 204
    anonymous.cookies.set(COOKIE_NAME, token)
    assert (await anonymous.get("/me")).status_code == 401
    async with db[0]() as session:
        row = (await session.execute(select(Session).where(Session.token == db[3]["student"]))).scalar_one()
        row.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        await session.commit()
    assert (await member.get("/me")).status_code == 401
    assert (
        await admin.post("/auth/local", json={"username": "admin", "password": "regression-password-only"})
    ).status_code == 200
    monkeypatch.setattr(local_admin, "SessionLocal", db[0])
    await local_admin.save_admin("admin", hash_password("replacement-password-only"))
    assert (await admin.get("/me")).status_code == 401


async def test_13_dashboard_authorization(clients, db):
    admin, member, anonymous = clients
    for path in ("/me", "/admin/users", "/admin/audit", "/integrations"):
        assert (await anonymous.get(path)).status_code == 401
    for method, path, body in [
        ("GET", "/admin/users", None),
        ("GET", "/admin/audit", None),
        ("GET", "/integrations", None),
        ("GET", "/integrations/notion/connect", None),
        ("DELETE", "/integrations/notion", None),
        ("PATCH", f"/admin/users/{db[1].id}", {"calendar_email": "stolen@example.org"}),
        ("PATCH", f"/admin/users/{db[2].id}/role", {"role": "admin"}),
    ]:
        response = await member.request(method, path, json=body)
        assert response.status_code == 403, (path, response.text)
    assert (await admin.get("/me")).json()["calendar_email"] is None
    assert (await member.get("/me")).json()["role"] == "student"


async def test_15_dashboard_connection_lifecycle(clients, monkeypatch):
    admin = clients[0]
    account = SimpleNamespace(id="ci-account", status="ACTIVE")
    provider = Mock()
    provider.connected_accounts.list.return_value = SimpleNamespace(items=[account])
    provider.connected_accounts.delete.side_effect = lambda **kw: setattr(account, "status", "INACTIVE")
    monkeypatch.setattr(integrations, "_get_composio", lambda: provider)
    monkeypatch.setattr(
        integrations, "_authorize", lambda toolkit, callback: "https://connect.example/authorize"
    )
    assert (await admin.get("/integrations/notion/connect")).headers[
        "location"
    ] == "https://connect.example/authorize"
    assert (await admin.get("/integrations/notion/callback?status=success")).status_code == 400
    callback = "/integrations/notion/callback?status=success&connected_account_id=ci-account"
    assert (await admin.get(callback)).status_code == 307
    assert (await admin.get("/integrations")).json()[0]["provider"] == "notion"
    assert (await admin.delete("/integrations/notion")).status_code == 204
    provider.connected_accounts.delete.assert_called_once_with(nanoid="ci-account")
    assert (await admin.get("/integrations")).json() == []
    assert (await admin.get(callback)).status_code == 400


async def test_18_dashboard_concurrent_users(clients):
    admin, member, _ = clients
    responses = await asyncio.gather(
        admin.patch("/me", json={"calendar_email": "first@example.org"}),
        member.patch("/me", json={"calendar_email": "second@example.org"}),
    )
    assert [r.status_code for r in responses] == [200, 200]
    assert (await admin.get("/me")).json()["calendar_email"] == "first@example.org"
    assert (await member.get("/me")).json()["calendar_email"] == "second@example.org"


async def test_19_dashboard_invalid_input(clients):
    admin, member, _ = clients
    for value in ("not-an-email", "a" * 255 + "@example.org", "x@example.org\nBcc: other@example.org"):
        assert (await member.patch("/me", json={"calendar_email": value})).status_code == 422
    assert (await member.patch("/me", json={"role": "admin"})).status_code == 422
    assert (await member.get("/me")).json()["role"] == "student"
    assert (await admin.delete("/admin/users/not-a-uuid")).status_code == 422
    # SQL-looking display names are data, not executable SQL.
    name = "Robert'); DROP TABLE users;--"
    created = await admin.post("/admin/users", json={"uw_email": "sql@example.org", "display_name": name})
    assert created.status_code == 201, created.text
    assert (await admin.get("/admin/users")).json()["total"] == 3


async def test_20_dashboard_audit_metadata_only(clients, db):
    async with db[0]() as session:
        await record_action(
            session,
            discord_id="1",
            guild_id="10",
            channel_id="40",
            tool="notion.search",
            status="ok",
            duration_ms=42,
        )
        await session.commit()
        columns = (
            (
                await session.execute(
                    text(
                        "SELECT column_name FROM information_schema.columns WHERE table_name='agent_actions'"
                    )
                )
            )
            .scalars()
            .all()
        )
    assert not {"input", "output", "content", "tool_input", "tool_result"}.intersection(columns)
    row = (await clients[0].get("/admin/audit")).json()["items"][0]
    assert row["discord_id"] == "1" and row["tool"] == "notion.search" and row["status"] == "ok"
    assert "password_hash" not in (await clients[0].get("/me")).text


async def test_24_bot_dashboard_email_directory_consistency(clients, db):
    member = clients[1]
    assert (await member.patch("/me", json={"calendar_email": "shared@example.org"})).status_code == 200
    async with db[0]() as session:
        assert (await find_user_by_discord_id(session, "2"))["calendar_email"] == "shared@example.org"
        assert await set_calendar_email(session, "2", "discord@example.org") == 1
        await session.commit()
    assert (await member.get("/me")).json()["calendar_email"] == "discord@example.org"
    assert (await member.patch("/me", json={"calendar_email": None})).status_code == 200
    async with db[0]() as session:
        assert (await find_user_by_discord_id(session, "2"))["calendar_email"] is None


async def test_25_dashboard_login_throttle(clients, monkeypatch):
    anonymous = clients[2]
    clock = Mock(return_value=100.0)
    monkeypatch.setattr(auth, "time", SimpleNamespace(monotonic=clock))
    credentials = {"username": "admin", "password": "incorrect-password"}
    for _ in range(5):
        assert (await anonymous.post("/auth/local", json=credentials)).status_code == 401
    limited = await anonymous.post("/auth/local", json=credentials)
    assert limited.status_code == 429 and limited.headers["retry-after"] == "60"
    clock.return_value = 161.0
    assert (await anonymous.post("/auth/local", json=credentials)).status_code == 401


async def test_26_dashboard_audit_filter_and_pagination(clients, db):
    async with db[0]() as session:
        for tool, status in [("notion.search", "ok"), ("notion.search", "error"), ("calendar.list", "ok")]:
            await record_action(
                session,
                discord_id="1",
                guild_id="10",
                channel_id="40",
                tool=tool,
                status=status,
                duration_ms=1,
            )
        await session.commit()
    admin = clients[0]
    result = (await admin.get("/admin/audit?tool=notion.search&status=error&limit=1")).json()
    assert result["total"] == 1 and result["items"][0]["status"] == "error"
    assert (await admin.get("/admin/audit?offset=3")).json()["items"] == []
    assert (await admin.get("/admin/audit?limit=501")).status_code == 422


async def test_27_dashboard_duplicate_users_and_crud(clients):
    admin = clients[0]
    body = {"uw_email": "unique@example.org", "discord_id": "123", "display_name": "Unique"}
    response = await admin.post("/admin/users", json=body)
    assert response.status_code == 201, response.text
    user_id = response.json()["id"]
    assert (await admin.post("/admin/users", json=body)).status_code == 409
    assert (
        await admin.post("/admin/users", json={**body, "uw_email": "another@example.org"})
    ).status_code == 409
    assert (await admin.patch(f"/admin/users/{user_id}", json={"display_name": "Updated"})).status_code == 200
    assert (await admin.patch(f"/admin/users/{user_id}/role", json={"role": "admin"})).status_code == 200
    assert (await admin.delete(f"/admin/users/{user_id}")).status_code == 204
    assert (await admin.get("/admin/users")).json()["total"] == 2


async def test_28_dashboard_production_cookie_flags(monkeypatch):
    monkeypatch.setenv("ENV", "production")
    response = Response()
    set_session_cookie(response, "regression-token")
    cookie = response.headers["set-cookie"].lower()
    for flag in ("secure", "httponly", "samesite=lax", "path=/", "max-age="):
        assert flag in cookie
