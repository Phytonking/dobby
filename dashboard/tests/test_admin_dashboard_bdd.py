"""Behaviour specs for issue #4: service status, calendar emails and adding teammates.

Scenarios live in features/*.feature; Composio and the database are faked at the same
boundaries the rest of the dashboard suite uses.
"""

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
from pytest_bdd import given, parsers, scenarios, then, when

from dashboard.auth import get_current_user, require_admin
from dashboard.routers import integrations

from .conftest import FakeDB, make_user

scenarios("features/service_status.feature", "features/people.feature")

MOD = "dashboard.routers.integrations"


class RecordingDB(FakeDB):
    """FakeDB that remembers statements and gives new rows the defaults a flush would."""

    def __init__(self, results=None):
        super().__init__(results)
        self.statements = []

    async def execute(self, stmt, params=None):
        self.statements.append(stmt)
        return await super().execute(stmt, params)

    def add(self, obj):
        if getattr(obj, "id", None) is None:
            obj.id = uuid.uuid4()
        if getattr(obj, "created_at", None) is None:
            obj.created_at = datetime.now(timezone.utc)
        super().add(obj)


@pytest.fixture()
def world(app, client):
    state = SimpleNamespace(
        app=app,
        client=client,
        user=None,
        records=[],
        pages=[[]],
        composio_down=False,
        db=None,
        response=None,
    )
    yield state
    app.dependency_overrides.clear()


def _sign_in(world, role):
    world.user = make_user(role=role, calendar_email=None)

    async def current():
        return world.user

    world.app.dependency_overrides[get_current_user] = current
    # require_admin depends on get_current_user, so the override above is enough; drop any
    # earlier direct override so a later "student instead" step really is a student.
    world.app.dependency_overrides.pop(require_admin, None)


# ---------------------------------------------------------------------------
# Given
# ---------------------------------------------------------------------------


@given("I am signed in as an admin")
def signed_in_admin(world):
    _sign_in(world, "admin")


@given("I am signed in as a student")
@given("I am signed in as a student instead")
def signed_in_student(world):
    _sign_in(world, "student")


@given("the dashboard has no record of any connection")
def no_records(world):
    world.records = []


@given(parsers.parse('the dashboard has a record for "{provider}"'))
def a_record(world, provider):
    world.records.append(
        SimpleNamespace(
            provider=provider, connected_by=None, connected_at=datetime(2026, 1, 5, tzinfo=timezone.utc)
        )
    )


def _account(toolkit, status, disabled=False):
    return SimpleNamespace(
        id=f"ca_{uuid.uuid4().hex[:6]}",
        status=status,
        is_disabled=disabled,
        toolkit=SimpleNamespace(slug=toolkit),
        created_at="2026-02-01T10:00:00Z",
    )


@given(parsers.parse('Composio has a "{status}" "{toolkit}" account for Dobby'))
def composio_account(world, status, toolkit):
    world.pages[0].append(_account(toolkit, status))


@given(parsers.parse('Composio has a disabled "{status}" "{toolkit}" account for Dobby'))
def composio_disabled_account(world, status, toolkit):
    world.pages[0].append(_account(toolkit, status, disabled=True))


@given("Composio has no accounts for Dobby")
def composio_empty(world):
    world.pages = [[]]


@given(parsers.parse('Composio returns "{first}" on page one and "{second}" on page two'))
def composio_two_pages(world, first, second):
    world.pages = [[_account(first, "ACTIVE")], [_account(second, "ACTIVE")]]


@given("Composio is unreachable")
def composio_unreachable(world):
    world.composio_down = True


# ---------------------------------------------------------------------------
# When
# ---------------------------------------------------------------------------


@when("I open the service accounts page")
def open_service_accounts(world):
    pages = [
        SimpleNamespace(items=items, next_cursor=("next" if i < len(world.pages) - 1 else None))
        for i, items in enumerate(world.pages)
    ]
    fake = SimpleNamespace(connected_accounts=SimpleNamespace(list=Mock(side_effect=pages)))
    get = Mock(side_effect=RuntimeError("composio down")) if world.composio_down else Mock(return_value=fake)
    world.db = RecordingDB(results=[world.records])
    with patch(f"{MOD}.COMPOSIO_API_KEY", "test-key"), patch(f"{MOD}._get_composio", get):
        world.response = world.client(world.db).get("/integrations")
    world.list_calls = fake.connected_accounts.list.call_args_list


@when(parsers.parse('I save my calendar email as "{value}"'))
def save_calendar_email(world, value):
    world.db = RecordingDB()
    world.response = world.client(world.db).patch("/me", json={"calendar_email": value})


@when("I clear my calendar email")
def clear_calendar_email(world):
    save_calendar_email(world, "")


def _add(world, body):
    world.db = RecordingDB(results=[None])
    world.response = world.client(world.db).post("/admin/users", json=body)


@when(parsers.parse('I add "{name}" with Discord ID "{discord_id}" and no Google email'))
def add_discord_only(world, name, discord_id):
    _add(world, {"display_name": name, "discord_id": discord_id})


@when(parsers.parse('I add "{name}" with Google email "{email}"'))
def add_google(world, name, email):
    _add(world, {"display_name": name, "uw_email": email})


@when(parsers.parse('I add "{name}" with no Google email and no Discord ID'))
def add_neither(world, name):
    _add(world, {"display_name": name})


@when(parsers.parse('someone signs in with Google as "{email}"'))
def google_sign_in(world, email):
    world.db = RecordingDB(results=[None])
    token = {"userinfo": {"email": email, "email_verified": True}}
    with patch("dashboard.routers.auth.oauth.google.authorize_access_token", AsyncMock(return_value=token)):
        world.response = world.client(world.db).get("/auth/google/callback", follow_redirects=False)


# ---------------------------------------------------------------------------
# Then
# ---------------------------------------------------------------------------


def _statuses(world):
    assert world.response.status_code == 200, world.response.text
    return {row["provider"]: row for row in world.response.json()}


@then(parsers.parse('"{provider}" shows as "{status}"'))
def shows_as(world, provider, status):
    assert _statuses(world)[provider]["status"] == status


@then("every status is verified with Composio")
def all_verified(world):
    rows = _statuses(world)
    assert set(rows) == set(integrations.SUPPORTED_PROVIDERS)
    assert all(row["verified"] for row in rows.values())
    # Asked about Dobby's own entity only.
    assert all(call.kwargs["user_ids"] == [integrations.ENTITY_ID] for call in world.list_calls)


@then("no status is verified with Composio")
def none_verified(world):
    assert not any(row["verified"] for row in _statuses(world).values())


@then(parsers.parse("the request is refused with {code:d}"))
def refused(world, code):
    assert world.response.status_code == code, world.response.text


@then("the request succeeds")
def succeeds(world):
    assert world.response.status_code == 200, world.response.text


@then(parsers.parse('my calendar email is stored as "{value}"'))
def stored_as(world, value):
    assert world.user.calendar_email == value
    assert world.response.json()["calendar_email"] == value
    assert world.db.committed


@then("my calendar email is cleared")
def cleared(world):
    assert world.user.calendar_email is None
    assert world.response.json()["calendar_email"] is None


@then(parsers.parse('the reason mentions "{text}"'))
def reason_mentions(world, text):
    assert text in world.response.text


@then("nothing is saved")
def nothing_saved(world):
    assert not world.db.committed
    assert world.user.calendar_email is None


@then("the user is created")
def user_created(world):
    assert world.response.status_code == 201, world.response.text
    assert world.db.committed


@then("the new user has no Google email")
def no_google_email(world):
    assert world.response.json()["uw_email"] is None


@then(parsers.parse('the new user\'s Google email is "{email}"'))
def google_email_is(world, email):
    assert world.response.json()["uw_email"] == email
    assert world.db.added[0].uw_email == email


@then("the user lookup compares emails case-insensitively")
def lookup_case_insensitive(world):
    # The unknown address is refused, but only after a lower()-on-both-sides lookup.
    assert world.response.status_code == 403
    sql = world.db.statements[0].compile(compile_kwargs={"literal_binds": True})
    assert "lower(users.uw_email) = 'jane@gmail.com'" in str(sql)
