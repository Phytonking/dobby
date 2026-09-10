from datetime import datetime, timedelta, timezone
from unittest.mock import Mock
import pytest
from bot.config import Config
from bot.models import Plan, UserError, event_body
from bot.service import Scheduler
from bot.calendar import Calendar


def times():
    start = datetime.now(timezone.utc) + timedelta(days=10)
    return start.isoformat(), (start + timedelta(hours=1)).isoformat()


@pytest.fixture
def existing():
    start, end = times()
    return {
        "id": "abc123",
        "etag": '"v1"',
        "summary": "Sync",
        "start": {"dateTime": start},
        "end": {"dateTime": end},
        "attendees": [{"email": "team@example.com"}],
        "description": "Keep me",
    }


def test_permissions_are_fail_closed():
    config = Config("", 10, frozenset({20}), frozenset({30}), frozenset({40}), "", "", "", "", "UTC")
    assert config.allows(10, 20, [], 40)
    assert config.allows(10, 99, [30], 40)
    assert not config.allows(11, 20, [30], 40)
    assert not config.allows(None, 20, [30], 40)
    assert not config.allows(10, 99, [], 40)
    assert not config.allows(10, 20, [30], 41)


@pytest.mark.parametrize(
    "start,end",
    [
        ("2030-01-01T10:00:00", "2030-01-01T11:00:00"),
        ("2030-01-01T10:00:00Z", "2030-01-01T09:00:00Z"),
        ("2020-01-01T10:00:00Z", "2020-01-01T11:00:00Z"),
        ("2030-01-01T10:00:00Z", "2030-01-03T10:00:00Z"),
        (None, None),
        ("garbage", "garbage"),
    ],
)
def test_invalid_times_rejected(start, end):
    with pytest.raises(UserError):
        event_body(Plan(action="create", summary="Sync", start=start, end=end), None, "UTC")


def test_patch_preserves_unmentioned_fields(existing):
    body = event_body(Plan(action="update", summary="New"), existing, "UTC")
    assert body == {"summary": "New"}
    assert "attendees" not in body


def test_invitees_become_attendees_and_updates_merge_existing_guests(existing):
    start, end = times()
    body = event_body(Plan(action="create", summary="Sync", start=start, end=end), None, "UTC", ["a@x.com"])
    assert body["attendees"] == [{"email": "a@x.com"}]
    body = event_body(Plan(action="update"), existing, "UTC", ["Team@example.com", "b@x.com", "b@x.com"])
    assert body["attendees"] == [{"email": "team@example.com"}, {"email": "b@x.com"}]
    with pytest.raises(UserError, match="No changes"):
        event_body(Plan(action="update"), existing, "UTC", ["team@example.com"])


def test_unknown_invitee_names_raise_need_contacts_and_known_ones_resolve():
    from bot.contacts import Contacts
    from bot.service import NeedContacts

    class Memory(Contacts):
        def __init__(self, known):
            super().__init__("unused.json")
            self.known = known

        def _read(self):
            return {k: {"email": v, "display": k} for k, v in self.known.items()}

    planner, calendar = Mock(), Mock()
    start, end = times()
    planner.plan.return_value = Plan(
        action="create", summary="Sync", start=start, end=end, invitees=["Maya", "leo@y.org", "Bob"]
    )
    scheduler = Scheduler(planner, calendar, "UTC", Memory({"maya": "maya@x.com"}))
    with pytest.raises(NeedContacts) as caught:
        scheduler.prepare("invite them", None, 1)
    assert caught.value.names == ["Bob"]
    planner.plan.return_value.invitees = ["Maya", "leo@y.org"]
    proposal = scheduler.prepare("invite them", None, 1)
    assert proposal.attendees == ("maya@x.com", "leo@y.org")
    assert proposal.body["attendees"] == [{"email": "maya@x.com"}, {"email": "leo@y.org"}]


def test_create_defaults_to_one_hour():
    start, _ = times()
    body = event_body(Plan(action="create", summary="Sync", start=start), None, "UTC")
    assert (
        datetime.fromisoformat(body["end"]["dateTime"]) - datetime.fromisoformat(body["start"]["dateTime"])
    ) == timedelta(hours=1)


def test_explicit_duration_overrides_default():
    start, _ = times()
    end = (datetime.fromisoformat(start) + timedelta(minutes=30)).isoformat()
    body = event_body(Plan(action="create", summary="Sync", start=start, end=end), None, "UTC")
    assert body["end"]["dateTime"] == end


def test_move_without_end_preserves_original_duration(existing):
    start = (datetime.fromisoformat(existing["start"]["dateTime"]) + timedelta(days=1)).isoformat()
    body = event_body(Plan(action="update", start=start), existing, "UTC")
    assert (
        datetime.fromisoformat(body["end"]["dateTime"]) - datetime.fromisoformat(body["start"]["dateTime"])
    ) == timedelta(hours=1)


def test_preview_times_are_normalized_to_team_zone():
    body = event_body(
        Plan(action="create", summary="Sync", start="2030-07-01T16:00:00Z"), None, "America/Denver"
    )
    assert body["start"]["dateTime"] == "2030-07-01T10:00:00-06:00"


@pytest.mark.parametrize("action", ["delete", "update"])
def test_mutation_without_event_or_title_asks_for_the_title(action):
    from bot.service import NeedTitle

    planner, calendar = Mock(), Mock()
    planner.plan.return_value = Plan(action=action, summary="New")
    with pytest.raises(NeedTitle, match="exact event"):
        Scheduler(planner, calendar, "UTC").prepare("change something", None, 123)
    calendar.list.assert_not_called()
    calendar.apply.assert_not_called()


def upcoming(id, summary, offset_days=5):
    start = datetime.now(timezone.utc) + timedelta(days=offset_days)
    return {
        "id": id,
        "etag": '"v1"',
        "summary": summary,
        "start": {"dateTime": start.isoformat()},
        "end": {"dateTime": (start + timedelta(hours=1)).isoformat()},
    }


def test_delete_by_title_resolves_a_clear_match_and_marks_the_proposal():
    planner, calendar = Mock(), Mock()
    calendar.list.return_value = [upcoming("a", "Design review"), upcoming("b", "Lunch")]
    planner.plan.return_value = Plan(action="delete", target_title="design review")
    proposal = Scheduler(planner, calendar, "UTC").prepare("delete the design review", None, 1)
    assert (proposal.action, proposal.event_id, proposal.etag) == ("delete", "a", '"v1"')
    assert proposal.resolved_by_title and proposal.existing["id"] == "a"
    calendar.get.assert_not_called()


def test_title_from_reply_overrides_gemini_and_ambiguity_lists_candidates():
    from bot.service import ChooseEvent

    planner, calendar = Mock(), Mock()
    calendar.list.return_value = [upcoming("a", "Team sync", 3), upcoming("b", "Team sync", 4)]
    planner.plan.return_value = Plan(action="delete", target_title=None)
    scheduler = Scheduler(planner, calendar, "UTC")
    with pytest.raises(ChooseEvent) as caught:
        scheduler.prepare("delete it", None, 1, title="team sync")
    assert [c["id"] for c in caught.value.candidates] == ["a", "b"]
    assert "event_id:a" in str(caught.value)
    with pytest.raises(UserError, match="Nothing|no upcoming|found no"):
        scheduler.prepare("delete it", None, 1, title="quarterly offsite")


def test_recurrence_rejected_before_gemini(existing):
    planner, calendar = Mock(), Mock()
    calendar.get.return_value = {**existing, "recurringEventId": "parent"}
    with pytest.raises(UserError, match="single"):
        Scheduler(planner, calendar, "UTC").prepare("delete", "abc123", 123)
    planner.plan.assert_not_called()


def test_preview_does_not_write_and_operation_id_is_stable():
    planner, calendar = Mock(), Mock()
    start, end = times()
    planner.plan.return_value = Plan(action="create", summary="Sync", start=start, end=end)
    scheduler = Scheduler(planner, calendar, "UTC")
    first = scheduler.prepare("create", None, 123)
    second = scheduler.prepare("create", None, 123)
    assert first.operation_id == second.operation_id
    calendar.apply.assert_not_called()


def test_calendar_patch_uses_etag_and_rechecks_conflicts(existing):
    planner, api = Mock(), Mock()
    api.get.return_value = existing
    planner.plan.return_value = Plan(action="update", summary="Changed")
    proposal = Scheduler(planner, api, "UTC").prepare("rename", "abc123", 123)
    calendar = Calendar.__new__(Calendar)
    calendar.call = Mock(return_value={})
    calendar.check_conflicts = Mock()
    calendar.apply(proposal)
    calendar.check_conflicts.assert_called_once_with({"summary": "Changed"}, "abc123")
    args, kwargs = calendar.call.call_args
    assert args == ("PATCH", "abc123")
    assert kwargs["headers"] == {"If-Match": '"v1"'}
    assert kwargs["json"] == {"summary": "Changed"}
    assert kwargs["params"]["sendUpdates"] == "all"


def test_conflicts_ignore_self_and_transparent_but_block_all_day(existing):
    calendar = Calendar.__new__(Calendar)
    calendar.list = Mock(return_value=[existing, {"id": "other", "transparency": "transparent"}])
    calendar.check_conflicts(existing, "abc123")
    calendar.list.return_value.append({"id": "busy", "start": {"date": "2030-01-01"}})
    with pytest.raises(UserError, match="busy"):
        calendar.check_conflicts(existing, "abc123")


def test_pagination_follows_even_empty_page():
    calendar = Calendar.__new__(Calendar)
    calendar.call = Mock(side_effect=[{"items": [], "nextPageToken": "next"}, {"items": [{"id": "x"}]}])
    assert calendar.list("start", "end") == [{"id": "x"}]
    assert calendar.call.call_args.kwargs["params"]["pageToken"] == "next"


def test_http_error_never_leaks_response():
    calendar = Calendar.__new__(Calendar)
    calendar.base = "https://example.com/events"
    calendar.session = Mock()
    calendar.session.request.return_value = Mock(status_code=403, ok=False, text="secret-token")
    with pytest.raises(UserError, match="HTTP 403") as error:
        calendar.get("abc")
    assert "secret-token" not in str(error.value)


def test_stale_event_rejected():
    calendar = Calendar.__new__(Calendar)
    calendar.base = "https://example.com/events"
    calendar.session = Mock()
    calendar.session.request.return_value = Mock(status_code=412)
    with pytest.raises(UserError, match="changed since"):
        calendar.call("PATCH", "abc")
