"""Dobby's service-account routes. Composio is faked at the router's helper boundary."""

from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from dashboard.auth import get_current_user
from dashboard.routers import integrations

from .conftest import FakeDB, make_user

MOD = "dashboard.routers.integrations"


@pytest.fixture()
def as_role(app, client):
    def login(role, db=None):
        user = make_user(role=role)

        async def fake_current_user():
            return user

        app.dependency_overrides[get_current_user] = fake_current_user
        return client(db or FakeDB())

    with patch(f"{MOD}.COMPOSIO_API_KEY", "test-key"):
        yield login


def _get(c, path):
    return c.get(path, follow_redirects=False)


# ---------------------------------------------------------------------------
# Connect
# ---------------------------------------------------------------------------


def test_connect_and_list_are_admin_only(as_role):
    c = as_role("student")
    with patch(f"{MOD}._authorize") as authorize:
        assert _get(c, "/integrations/google_calendar/connect").status_code == 403
        assert c.get("/integrations").status_code == 403
    authorize.assert_not_called()


def test_connect_redirects_to_composio_with_api_callback(as_role):
    with patch(f"{MOD}._authorize", return_value="https://connect.composio.dev/link/abc") as authorize:
        r = _get(as_role("admin"), "/integrations/google_calendar/connect")

    assert r.status_code == 307
    assert r.headers["location"] == "https://connect.composio.dev/link/abc"
    toolkit, callback = authorize.call_args.args
    assert toolkit == "googlecalendar"
    assert callback.endswith("/integrations/google_calendar/callback")


def test_connect_unknown_provider_is_400_and_composio_failure_is_502(as_role):
    c = as_role("admin")
    assert _get(c, "/integrations/dropbox/connect").status_code == 400
    with patch(f"{MOD}._authorize", side_effect=RuntimeError("down")):
        assert _get(c, "/integrations/notion/connect").status_code == 502


# ---------------------------------------------------------------------------
# Callback: every failure lands back on the page with a banner, never a JSON error
# ---------------------------------------------------------------------------

CALLBACK = "/integrations/google_calendar/callback"
OK_QUERY = "?status=success&connected_account_id=ca_1"


@pytest.mark.parametrize("query", ["", "?status=failed&connected_account_id=ca_1", "?status=success"])
def test_incomplete_oauth_redirects_with_error_without_touching_composio(as_role, query):
    db = FakeDB()
    with patch(f"{MOD}._connection_is_active") as active:
        r = _get(as_role("admin", db), CALLBACK + query)

    assert r.status_code == 307
    assert r.headers["location"].endswith("/dashboard/integrations?error=google_calendar")
    active.assert_not_called()
    assert not db.added


@pytest.mark.parametrize("verify", [Mock(return_value=False), Mock(side_effect=RuntimeError("down"))])
def test_unverified_connection_redirects_with_error_and_records_nothing(as_role, verify):
    db = FakeDB()
    with patch(f"{MOD}._connection_is_active", verify):
        r = _get(as_role("admin", db), CALLBACK + OK_QUERY)

    assert r.headers["location"].endswith("/dashboard/integrations?error=google_calendar")
    verify.assert_called_once_with("googlecalendar", "ca_1")
    assert not db.added


def test_verified_connection_is_recorded_and_lands_on_the_canonical_dashboard(as_role, monkeypatch):
    monkeypatch.setenv("DASHBOARD_URL", "http://100.64.0.10:3000,http://localhost:3000")
    db = FakeDB(results=[None])
    with patch(f"{MOD}._connection_is_active", return_value=True):
        r = _get(as_role("admin", db), CALLBACK + OK_QUERY)

    assert r.headers["location"] == "http://100.64.0.10:3000/dashboard/integrations?connected=google_calendar"
    assert db.committed
    assert db.added[0].provider == "google_calendar"


def test_local_mode_lands_on_localhost_whatever_dashboard_url_says(as_role, monkeypatch):
    monkeypatch.setenv("DASHBOARD_URL", "http://100.64.0.10:3000")
    monkeypatch.setenv("LOCAL_MODE", "true")
    with patch(f"{MOD}._connection_is_active", return_value=True):
        r = _get(as_role("admin", FakeDB(results=[None])), CALLBACK + OK_QUERY)

    assert r.headers["location"] == "http://localhost:3000/dashboard/integrations?connected=google_calendar"


def test_storage_failure_after_verification_redirects_with_error(as_role):
    class FailingDB(FakeDB):
        async def commit(self):
            raise RuntimeError("db down")

    with patch(f"{MOD}._connection_is_active", return_value=True):
        r = _get(as_role("admin", FailingDB(results=[None])), CALLBACK + OK_QUERY)

    assert r.headers["location"].endswith("/dashboard/integrations?error=google_calendar")


# ---------------------------------------------------------------------------
# Disconnect
# ---------------------------------------------------------------------------


def test_disconnect_is_admin_only(as_role):
    with patch(f"{MOD}._disconnect_accounts") as disconnect:
        assert as_role("student").delete("/integrations/google_calendar").status_code == 403
    disconnect.assert_not_called()


def test_disconnect_removes_composio_accounts_then_the_local_row(as_role):
    row = SimpleNamespace(provider="google_calendar")
    db = FakeDB(results=[row])
    with patch(f"{MOD}._disconnect_accounts") as disconnect:
        r = as_role("admin", db).delete("/integrations/google_calendar")

    assert r.status_code == 204
    disconnect.assert_called_once_with("googlecalendar")
    assert db.deleted == [row]


def test_disconnect_without_a_local_row_still_cleans_composio(as_role):
    # A callback that never landed leaves live Composio connections and no row.
    db = FakeDB(results=[None])
    with patch(f"{MOD}._disconnect_accounts") as disconnect:
        r = as_role("admin", db).delete("/integrations/google_calendar")

    assert r.status_code == 204
    disconnect.assert_called_once_with("googlecalendar")
    assert db.deleted == []


def test_disconnect_composio_failure_is_502_and_keeps_the_row(as_role):
    db = FakeDB(results=[SimpleNamespace(provider="notion")])
    with patch(f"{MOD}._disconnect_accounts", side_effect=RuntimeError("down")):
        assert as_role("admin", db).delete("/integrations/notion").status_code == 502
    assert db.deleted == []


def test_disconnect_unknown_provider_is_400(as_role):
    with patch(f"{MOD}._disconnect_accounts") as disconnect:
        assert as_role("admin").delete("/integrations/dropbox").status_code == 400
    disconnect.assert_not_called()


# ---------------------------------------------------------------------------
# Composio helpers against a fake client
# ---------------------------------------------------------------------------


def _fake_client(*pages):
    listed = [SimpleNamespace(items=items, next_cursor=cursor) for items, cursor in pages]
    return SimpleNamespace(connected_accounts=SimpleNamespace(list=Mock(side_effect=listed), delete=Mock()))


def test_disconnect_accounts_deletes_every_account_across_pages():
    accounts = [SimpleNamespace(id=f"ca_{i}") for i in range(3)]
    client = _fake_client((accounts[:2], "next"), (accounts[2:], None))
    with patch(f"{MOD}._get_composio", return_value=client):
        integrations._disconnect_accounts("googlecalendar")

    first, second = client.connected_accounts.list.call_args_list
    # No status filter: failed/expired leftovers are removed along with ACTIVE ones.
    assert first.kwargs == {"user_ids": [integrations.ENTITY_ID], "toolkit_slugs": ["googlecalendar"]}
    assert second.kwargs["cursor"] == "next"
    deleted = [c.kwargs["nanoid"] for c in client.connected_accounts.delete.call_args_list]
    assert deleted == ["ca_0", "ca_1", "ca_2"]


def test_connection_is_active_requires_the_exact_active_account():
    active = SimpleNamespace(id="ca_1", status="ACTIVE")
    client = _fake_client(([active], None))
    with patch(f"{MOD}._get_composio", return_value=client):
        assert integrations._connection_is_active("notion", "ca_1") is True

    kwargs = client.connected_accounts.list.call_args.kwargs
    assert kwargs["connected_account_ids"] == ["ca_1"] and kwargs["statuses"] == ["ACTIVE"]
    with patch(f"{MOD}._get_composio", return_value=_fake_client(([active], None))):
        assert integrations._connection_is_active("notion", "ca_other") is False
