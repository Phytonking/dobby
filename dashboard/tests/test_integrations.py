"""Integrations endpoints. Composio is the source of truth; faked at the router's helper boundary."""

from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from composio.exceptions import (
    ComposioConnectedAccountNotRevokableError,
    ComposioConnectedAccountRevocationNotSupportedError,
)

from dashboard.auth import get_current_user
from dashboard.routers import integrations

from .conftest import FakeDB, make_user

MOD = "dashboard.routers.integrations"


@pytest.fixture()
def as_role(app, client):
    def login(role):
        user = make_user(role=role)

        async def fake_current_user():
            return user

        app.dependency_overrides[get_current_user] = fake_current_user
        return client(FakeDB())

    with patch(f"{MOD}.COMPOSIO_API_KEY", "test-key"):
        yield login


# ---------------------------------------------------------------------------
# Connect
# ---------------------------------------------------------------------------


def _connect(c, headers=None):
    link = SimpleNamespace(redirect_url="https://connect.composio.dev/link/abc")
    session = SimpleNamespace(authorize=Mock(return_value=link))
    with patch(f"{MOD}._get_session", return_value=session):
        r = c.get("/integrations/googlecalendar/connect", headers=headers or {}, follow_redirects=False)
    return r, session.authorize


def test_connect_requires_admin(as_role):
    r, authorize = _connect(as_role("student"))

    assert r.status_code == 403
    authorize.assert_not_called()


def test_connect_callback_targets_browser_origin_not_dashboard_url(as_role):
    # Reproduces the bug: DASHBOARD_URL names another tailnet box, browser is on localhost.
    with patch(f"{MOD}.DASHBOARD_URL", "http://100.64.0.10:3000"):
        r, authorize = _connect(as_role("admin"), headers={"x-forwarded-host": "localhost:3000"})

    assert r.status_code == 307
    assert r.headers["location"] == "https://connect.composio.dev/link/abc"
    assert (
        authorize.call_args.kwargs["callback_url"]
        == "http://localhost:3000/api/integrations/googlecalendar/callback"
    )


def test_connect_callback_honors_forwarded_proto(as_role):
    headers = {"x-forwarded-host": "dobby.example", "x-forwarded-proto": "https"}
    _, authorize = _connect(as_role("admin"), headers=headers)

    assert (
        authorize.call_args.kwargs["callback_url"]
        == "https://dobby.example/api/integrations/googlecalendar/callback"
    )


def test_connect_without_proxy_headers_falls_back_to_dashboard_url(as_role):
    with patch(f"{MOD}.DASHBOARD_URL", "http://dash.example:3000"):
        _, authorize = _connect(as_role("admin"))

    assert (
        authorize.call_args.kwargs["callback_url"]
        == "http://dash.example:3000/api/integrations/googlecalendar/callback"
    )


# ---------------------------------------------------------------------------
# Callback — success or failure comes from Composio, never the redirect params
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "active, outcome",
    [
        ({"googlecalendar": "2026-10-01T22:59:43Z"}, "connected"),
        ({}, "error"),
        ({"notion": "2026-10-01T22:59:43Z"}, "error"),
    ],
)
def test_callback_reports_composio_state_not_redirect_params(as_role, active, outcome):
    c = as_role("admin")
    with patch(f"{MOD}._active_since", return_value=active):
        r = c.get("/integrations/googlecalendar/callback?status=success", follow_redirects=False)

    assert r.status_code == 307
    assert r.headers["location"] == f"/dashboard/integrations?{outcome}=googlecalendar"


def test_callback_reports_error_when_composio_unreachable(as_role):
    c = as_role("admin")
    with patch(f"{MOD}._active_since", side_effect=RuntimeError("down")):
        r = c.get("/integrations/googlecalendar/callback", follow_redirects=False)

    assert r.headers["location"] == "/dashboard/integrations?error=googlecalendar"


# ---------------------------------------------------------------------------
# List — shared state, visible to every logged-in user
# ---------------------------------------------------------------------------


def test_list_reflects_composio_for_any_logged_in_user(as_role):
    c = as_role("student")
    with patch(f"{MOD}._active_since", return_value={"googlecalendar": "2026-10-01T22:25:32Z"}):
        r = c.get("/integrations")

    assert r.status_code == 200
    body = r.json()
    assert [i["provider"] for i in body] == ["googlecalendar"]
    assert body[0]["connected_at"].startswith("2026-10-01T22:25:32")


def test_list_is_502_when_composio_unreachable(as_role):
    c = as_role("student")
    with patch(f"{MOD}._active_since", side_effect=RuntimeError("down")):
        assert c.get("/integrations").status_code == 502


# ---------------------------------------------------------------------------
# Disconnect
# ---------------------------------------------------------------------------


def test_disconnect_requires_admin(as_role):
    c = as_role("student")
    with patch(f"{MOD}._disconnect") as disconnect:
        r = c.delete("/integrations/googlecalendar")

    assert r.status_code == 403
    disconnect.assert_not_called()


def test_disconnect_removes_dobbys_composio_connections(as_role):
    c = as_role("admin")
    with patch(f"{MOD}._disconnect") as disconnect:
        r = c.delete("/integrations/googlecalendar")

    assert r.status_code == 204
    disconnect.assert_called_once_with("googlecalendar")


def test_disconnect_unknown_provider_is_400(as_role):
    c = as_role("admin")
    with patch(f"{MOD}._disconnect") as disconnect:
        assert c.delete("/integrations/dropbox").status_code == 400
    disconnect.assert_not_called()


def test_disconnect_composio_failure_is_502(as_role):
    c = as_role("admin")
    with patch(f"{MOD}._disconnect", side_effect=RuntimeError("down")):
        assert c.delete("/integrations/googlecalendar").status_code == 502


# ---------------------------------------------------------------------------
# Composio helpers against a fake client
# ---------------------------------------------------------------------------


def _account(id, slug="googlecalendar", created_at="2026-10-01T22:00:00Z"):
    return SimpleNamespace(id=id, toolkit=SimpleNamespace(slug=slug), created_at=created_at)


def _fake_client(*pages):
    listed = [SimpleNamespace(items=items, next_cursor=cursor) for items, cursor in pages]
    return SimpleNamespace(
        connected_accounts=SimpleNamespace(list=Mock(side_effect=listed), revoke=Mock(), delete=Mock())
    )


def test_disconnect_revokes_and_deletes_every_account_across_pages():
    client = _fake_client(([_account("ca_1"), _account("ca_2")], "next"), ([_account("ca_3")], None))
    client.connected_accounts.revoke.side_effect = [
        None,
        ComposioConnectedAccountNotRevokableError("expired"),
        ComposioConnectedAccountRevocationNotSupportedError("unsupported"),
    ]
    with patch(f"{MOD}._client", return_value=client):
        integrations._disconnect("googlecalendar")

    first, second = client.connected_accounts.list.call_args_list
    # No status filter: failed/expired leftovers are removed along with ACTIVE ones.
    assert first.kwargs == {
        "user_ids": [integrations.COMPOSIO_ENTITY_ID],
        "toolkit_slugs": ["googlecalendar"],
    }
    assert second.kwargs["cursor"] == "next"
    assert [c.args[0] for c in client.connected_accounts.delete.call_args_list] == ["ca_1", "ca_2", "ca_3"]


def test_disconnect_does_not_swallow_unexpected_errors():
    client = _fake_client(([_account("ca_1")], None))
    client.connected_accounts.revoke.side_effect = RuntimeError("network")
    with patch(f"{MOD}._client", return_value=client), pytest.raises(RuntimeError):
        integrations._disconnect("googlecalendar")

    client.connected_accounts.delete.assert_not_called()


def test_active_since_keeps_earliest_active_connection_per_toolkit():
    accounts = [
        _account("ca_1", created_at="2026-10-01T22:59:00Z"),
        _account("ca_2", created_at="2026-10-01T22:25:00Z"),
        _account("ca_3", slug="notion", created_at="2026-09-01T00:00:00Z"),
    ]
    client = _fake_client((accounts, None))
    with patch(f"{MOD}._client", return_value=client):
        since = integrations._active_since()

    assert since == {"googlecalendar": "2026-10-01T22:25:00Z", "notion": "2026-09-01T00:00:00Z"}
    assert client.connected_accounts.list.call_args.kwargs["statuses"] == ["ACTIVE"]
