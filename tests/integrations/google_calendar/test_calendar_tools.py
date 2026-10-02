"""Tests for the Google Calendar integration package."""

import asyncio
from unittest.mock import AsyncMock, patch

from bot.integrations.base import RunContext
from bot.integrations.google_calendar import ACTIONS, INTEGRATION
from bot.integrations.google_calendar.tools import LOOKUP_TOOL


def test_integration_shape():
    assert INTEGRATION.key == "google_calendar"
    assert all(a.startswith("GOOGLECALENDAR_") for a in ACTIONS)
    assert [t.name for t in INTEGRATION.local_tools] == ["lookup_calendar_email"]
    assert "never guess" in INTEGRATION.prompt.lower()


def test_lookup_tool_returns_match_or_not_found():
    ctx = RunContext(session=AsyncMock(), guild_id="10", channel_id="40", discord_user_id="1")

    async def run():
        found = {"display_name": "Maya Chen", "calendar_email": "maya@uw.edu"}
        with patch(
            "bot.integrations.google_calendar.tools.find_user_by_name", new=AsyncMock(return_value=found)
        ) as find:
            result = await LOOKUP_TOOL.handler(ctx, {"name": "Maya"})
            find.assert_awaited_once_with(ctx.session, "Maya")
        assert result == {"success": True, "found": True, "display_name": "Maya Chen", "email": "maya@uw.edu"}

        with patch(
            "bot.integrations.google_calendar.tools.find_user_by_name", new=AsyncMock(return_value=None)
        ):
            result = await LOOKUP_TOOL.handler(ctx, {"name": "Nobody"})
            assert result["success"] and not result["found"]
            assert "prepare the event now" in result["note"]
            assert ctx.missing_invitees["nobody"] == {"name": "Nobody", "discord_id": None}

    asyncio.run(run())
