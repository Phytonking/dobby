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
from bot.integrations.google_calendar.proposals import prepare_calendar_action

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


def context():
    return RunContext(
        AsyncMock(),
        "10",
        "40",
        "1",
        object(),
        SimpleNamespace(timezone="America/Los_Angeles", composio_entity="dobby"),
    )


def response(event=EVENT):
    return {"success": True, "data": {"response_data": deepcopy(event)}}


@pytest.mark.parametrize(
    "name,params",
    [
        (
            "GOOGLECALENDAR_CREATE_EVENT",
            {"summary": "Design review", "start_datetime": "2026-10-12T10:00:00"},
        ),
        ("GOOGLECALENDAR_PATCH_EVENT", {"event_id": "abc123", "start_time": "2026-10-12T14:00:00"}),
        ("GOOGLECALENDAR_DELETE_EVENT", {"event_id": "abc123"}),
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
            if "PATCH" in name:
                assert "location" not in sent and "attendees" not in sent

    asyncio.run(run())


def test_changed_event_is_not_written_after_preview():
    async def run():
        ctx = context()
        with patch(f"{MOD}.run_action", new=AsyncMock(return_value=response())) as backend:
            await prepare_calendar_action(ctx, "GOOGLECALENDAR_DELETE_EVENT", {"event_id": "abc123"})
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
            result = await prepare_calendar_action(ctx, "GOOGLECALENDAR_DELETE_EVENT", {"event_id": "abc123"})
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
                },
            )
        changes = ctx.pending[0].preview.split("**Changes:**")[1]
        assert "Title:" not in changes and "When:" not in changes
        assert "Room 2 → Room 3" in changes
        assert "Invitees removed: maya@example.com" in changes

    asyncio.run(run())
