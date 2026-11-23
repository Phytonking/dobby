"""Luma tools: reads call the API; invites are queued and send nothing until execute()."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.integrations.base import RunContext
from bot.integrations.luma.tools import MAX_INVITEES, draft_invites_tool, list_events

MOD = "bot.integrations.luma.tools"


def ctx(key="k"):
    return RunContext(
        session=None,
        guild_id="1",
        channel_id="2",
        discord_user_id="3",
        config=SimpleNamespace(luma_api_key=key),
    )


def test_list_events_slims_entries():
    data = {"entries": [{"event": {"id": "evt-1", "name": "Demo", "secret": "x"}}], "has_more": False}
    with patch(f"{MOD}.call", AsyncMock(return_value={"success": True, "data": data})):
        out = asyncio.run(list_events(ctx(), {}))
    assert out["events"] == [{"id": "evt-1", "name": "Demo"}]


def test_invites_queue_without_sending_then_send_on_execute():
    calls = AsyncMock(return_value={"success": True, "data": {"skipped": []}})
    with patch(f"{MOD}.call", calls):
        c = ctx()
        out = asyncio.run(draft_invites_tool(c, {"event_id": "evt-1", "emails": ["a@x.com"]}))
        assert out["queued"] and calls.await_count == 0
        assert asyncio.run(c.pending[0].execute())["success"] is True
        assert calls.await_args.args[:3] == ("k", "POST", "/v1/events/guests/send-invites")
        assert calls.await_args.kwargs["body"] == {"event_id": "evt-1", "guests": [{"email": "a@x.com"}]}


def test_invites_reject_bad_input():
    assert not asyncio.run(draft_invites_tool(ctx(), {"event_id": "evt-1", "emails": []}))["success"]
    many = [f"{i}@x.com" for i in range(MAX_INVITEES + 1)]
    assert not asyncio.run(draft_invites_tool(ctx(), {"event_id": "e", "emails": many}))["success"]
    assert not asyncio.run(
        draft_invites_tool(ctx(), {"event_id": "e", "emails": ["a@x.com"], "message": "x" * 201})
    )["success"]


def test_missing_key_is_reported():
    out = asyncio.run(list_events(ctx(""), {}))
    assert out["success"] is False and "LUMA_API_KEY" in out["error"]
