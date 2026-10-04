import asyncio
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from bot.AIModels import FunctionDeclaration
from bot.agent import Agent
from bot.integrations import build_registry
from bot.integrations.base import RunContext
from bot.integrations.google_calendar import INTEGRATION
from bot.integrations.google_calendar.proposals import prepare_calendar_action, with_deferred_invitees

MOD = "bot.integrations.google_calendar.proposals"
EVENT = {
    "id": "abc123",
    "etag": "v1",
    "summary": "Design review",
    "location": "Room 2",
    "start": {"dateTime": "2026-10-12T10:00:00-07:00"},
    "end": {"dateTime": "2026-10-12T11:00:00-07:00"},
    "attendees": [{"email": "maya@example.com"}],
}
CREATE = (
    "GOOGLECALENDAR_CREATE_EVENT",
    {"summary": "Design review", "start_datetime": "2026-10-12T10:00:00"},
)
PATCH = ("GOOGLECALENDAR_PATCH_EVENT", {"event_id": "abc123", "start_time": "2026-10-12T14:00:00"})
DELETE = ("GOOGLECALENDAR_DELETE_EVENT", {"event_id": "abc123"})


@pytest.fixture(autouse=True)
def registered(monkeypatch):
    """Who the database knows: `names` maps email -> display name, `users` maps Discord ID -> row."""
    people = {"names": {}, "users": {}}
    monkeypatch.setattr(
        f"{MOD}.display_names_for_emails",
        AsyncMock(side_effect=lambda session, emails: dict(people["names"])),
    )
    monkeypatch.setattr(
        f"{MOD}.find_user_by_discord_id",
        AsyncMock(side_effect=lambda session, discord_id: people["users"].get(str(discord_id))),
    )
    return people


def context(*known_ids):
    ctx = RunContext(
        AsyncMock(),
        "10",
        "40",
        "1",
        object(),
        SimpleNamespace(timezone="America/Los_Angeles", composio_entity="dobby"),
    )
    ctx.known_discord_ids.update(known_ids)
    return ctx


def response(event=EVENT):
    return {"success": True, "data": {"response_data": deepcopy(event)}}


@pytest.mark.parametrize(
    "name,params",
    [
        (CREATE[0], {**CREATE[1], "deferred_invitees": []}),
        (PATCH[0], {**PATCH[1], "deferred_invitees": []}),
        DELETE,
    ],
)
def test_real_registry_agent_path_queues_every_write(name, params):
    async def run():
        with patch(
            "bot.integrations.declarations_for",
            side_effect=lambda _, actions: [FunctionDeclaration(name=n, description=n) for n in actions],
        ):
            registry = build_registry(None, (INTEGRATION,))
        assert "GOOGLECALENDAR_UPDATE_EVENT" not in registry.owner
        agent = object.__new__(Agent)
        agent.registry = registry
        ctx = context()
        with patch(f"{MOD}.run_action", new=AsyncMock(return_value=response())) as backend:
            result = await agent._call(ctx, name, params)
            assert result["queued"] is True
            assert all(c.args[1] == "GOOGLECALENDAR_EVENTS_GET" for c in backend.await_args_list)
            preview = ctx.pending[0].preview
            assert "**Title:** Design review" in preview
            assert "UTC-07:00" in preview
            if "PATCH" in name:
                assert "**Changes:**" in preview and "2:00 PM" in preview and "3:00 PM" in preview
                assert "Room 2" in preview and "maya@example.com" in preview
            params["summary"] = "mutated after drafting"
            backend.return_value = response()
            await ctx.pending[0].execute()
            assert backend.await_args.args[1] == name
            sent = backend.await_args.args[2]
            assert sent.get("summary") != "mutated after drafting"
            assert "deferred_invitees" not in sent
            if "PATCH" in name:
                assert "location" not in sent and "attendees" not in sent

    asyncio.run(run())


def test_changed_event_is_not_written_after_preview():
    async def run():
        ctx = context()
        with patch(f"{MOD}.run_action", new=AsyncMock(return_value=response())) as backend:
            await prepare_calendar_action(ctx, *DELETE)
            backend.return_value = response({**EVENT, "etag": "v2", "summary": "Changed"})
            result = await ctx.pending[0].execute()
            assert not result["success"]
            assert all(c.args[1] == "GOOGLECALENDAR_EVENTS_GET" for c in backend.await_args_list)

    asyncio.run(run())


@pytest.mark.parametrize(
    "event", [None, {**EVENT, "recurrence": ["RRULE:FREQ=DAILY"]}, {**EVENT, "start": {"date": "2026-10-12"}}]
)
def test_missing_or_unsupported_event_never_queues(event):
    async def run():
        ctx = context()
        with patch(f"{MOD}.run_action", new=AsyncMock(return_value=response(event))):
            result = await prepare_calendar_action(ctx, *DELETE)
        assert not result["success"] and not ctx.pending

    asyncio.run(run())


def test_preview_shows_only_real_differences_and_removed_invitees():
    async def run():
        ctx = context()
        with patch(f"{MOD}.run_action", new=AsyncMock(return_value=response())):
            await prepare_calendar_action(
                ctx,
                "GOOGLECALENDAR_PATCH_EVENT",
                {
                    "event_id": "abc123",
                    "summary": "Design review",
                    "location": "Room 3",
                    "attendees": [],
                    "deferred_invitees": [],
                },
            )
        changes = ctx.pending[0].preview.split("**Changes:**")[1]
        assert "Title:" not in changes and "When:" not in changes
        assert "Room 2 → Room 3" in changes
        assert "Invitees removed: maya@example.com" in changes

    asyncio.run(run())


# --- who is being invited, as the confirmer sees it ---------------------------------------


def test_invitees_are_shown_with_their_registered_name(registered):
    registered["names"] = {"maya@example.com": "Maya Chen", "sam@uw.edu": "Sam Lee"}

    async def run():
        ctx = context()
        with patch(f"{MOD}.run_action", new=AsyncMock(return_value=response())):
            await prepare_calendar_action(
                ctx,
                "GOOGLECALENDAR_PATCH_EVENT",
                {
                    "event_id": "abc123",
                    "attendees": ["maya@example.com", "sam@uw.edu", "outsider@example.org"],
                    "deferred_invitees": [],
                },
            )
        preview = ctx.pending[0].preview
        assert (
            "**Invitees:** Maya Chen (maya@example.com), Sam Lee (sam@uw.edu), outsider@example.org"
            in preview
        )
        assert "Invitees added: Sam Lee (sam@uw.edu), outsider@example.org" in preview

    asyncio.run(run())


def test_unavailable_name_lookup_still_previews_bare_addresses(registered):
    async def run():
        ctx = context()
        with (
            patch(f"{MOD}.display_names_for_emails", new=AsyncMock(side_effect=RuntimeError("db down"))),
            patch(f"{MOD}.run_action", new=AsyncMock(return_value=response())),
        ):
            result = await prepare_calendar_action(ctx, *DELETE)
        assert result["queued"] and "maya@example.com" in ctx.pending[0].preview

    asyncio.run(run())


# --- deferred invitees ------------------------------------------------------------------------


@pytest.mark.parametrize("call", [CREATE, PATCH])
def test_create_and_edit_require_an_explicit_deferred_invitees_list(call):
    async def run():
        ctx = context()
        with patch(f"{MOD}.run_action", new=AsyncMock(return_value=response())):
            result = await prepare_calendar_action(ctx, *call)
        assert result["success"] is False and "deferred_invitees is required" in result["error"]
        assert not ctx.pending

    asyncio.run(run())


def test_delete_needs_no_deferred_invitees_and_ignores_any_given():
    async def run():
        ctx = context("11")
        with patch(f"{MOD}.run_action", new=AsyncMock(return_value=response())):
            result = await prepare_calendar_action(
                ctx, DELETE[0], {**DELETE[1], "deferred_invitees": [{"name": "Maya", "discord_id": "11"}]}
            )
        assert result["queued"] and "Waiting for email" not in ctx.pending[0].preview

    asyncio.run(run())


@pytest.mark.parametrize(
    "people,problem",
    [
        ("Maya", "must be a list"),
        ([{"discord_id": "11"}], "needs a name"),
        ([{"name": "  ", "discord_id": "11"}], "needs a name"),
        ([{"name": "Maya"}], "@mention"),
        ([{"name": "Maya", "discord_id": "maya"}], "@mention"),
        ([{"name": "Maya", "discord_id": "999"}], "did not come from an @mention or a lookup"),
    ],
)
def test_deferred_invitees_need_an_exact_discord_id_dobby_has_seen(people, problem):
    async def run():
        ctx = context("11")
        with patch(f"{MOD}.run_action", new=AsyncMock(return_value=response())) as backend:
            result = await prepare_calendar_action(ctx, CREATE[0], {**CREATE[1], "deferred_invitees": people})
        assert result["success"] is False and problem in result["error"]
        assert not ctx.pending
        backend.assert_not_awaited()

    asyncio.run(run())


def test_waiting_people_are_listed_once_with_their_registered_name_and_a_mention(registered):
    registered["users"] = {"11": {"display_name": "Maya Chen", "calendar_email": None}}

    async def run():
        ctx = context("11")
        result = await prepare_calendar_action(
            ctx,
            CREATE[0],
            {
                **CREATE[1],
                "deferred_invitees": [
                    {"name": "maya", "discord_id": 11},
                    {"name": "Maya again", "discord_id": "11"},
                ],
            },
        )
        assert result["queued"]
        preview = ctx.pending[0].preview
        assert preview.count("Maya Chen (<@11>)") == 1 and "again" not in preview
        assert "without waiting" in preview and "approve inviting them" in preview
        assert "automatically" not in preview

    asyncio.run(run())


def test_unregistered_waiting_person_is_shown_by_the_name_the_requester_used():
    async def run():
        ctx = context("77")
        await prepare_calendar_action(
            ctx, CREATE[0], {**CREATE[1], "deferred_invitees": [{"name": "Newcomer", "discord_id": "77"}]}
        )
        assert "**Waiting for email:** Newcomer (<@77>)" in ctx.pending[0].preview

    asyncio.run(run())


def test_a_waiting_person_the_model_could_only_name_by_mention_is_shown_once():
    async def run():
        ctx = context("77")
        await prepare_calendar_action(
            ctx, CREATE[0], {**CREATE[1], "deferred_invitees": [{"name": "<@77>", "discord_id": "77"}]}
        )
        preview = ctx.pending[0].preview
        assert "**Waiting for email:** <@77>\n" in preview and preview.count("77") == 1

    asyncio.run(run())


def test_each_run_keeps_its_own_waiting_people():
    """Nobody waits on a meeting the model did not name them for, whatever else it looked up."""

    async def run():
        ctx = context("11")
        await prepare_calendar_action(ctx, CREATE[0], {**CREATE[1], "deferred_invitees": []})
        await prepare_calendar_action(
            ctx,
            CREATE[0],
            {**CREATE[1], "deferred_invitees": [{"name": "Maya", "discord_id": "11"}]},
        )
        assert "Waiting for email" not in ctx.pending[0].preview
        assert "Waiting for email" in ctx.pending[1].preview

    asyncio.run(run())


def test_schema_requires_deferred_invitees_with_an_id_for_each_person():
    declaration = FunctionDeclaration(
        name="X", description="d", parameters={"type": "object", "properties": {"a": {}}, "required": ["a"]}
    )
    wrapped = with_deferred_invitees(declaration)
    schema = wrapped.parameters
    assert schema["required"] == ["a", "deferred_invitees"]
    item = schema["properties"]["deferred_invitees"]["items"]
    assert item["required"] == ["name", "discord_id"]
    assert "required" not in (declaration.parameters.get("properties", {}).get("deferred_invitees", {}))
    assert declaration.parameters["required"] == ["a"]  # the original is untouched
    bare = with_deferred_invitees(FunctionDeclaration(name="Y", description="d"))
    assert bare.parameters["required"] == ["deferred_invitees"]
