"""Tests for the Google Calendar integration package."""

import asyncio
from unittest.mock import AsyncMock, patch

from bot.integrations.base import RunContext
from bot.integrations.google_calendar import ACTIONS, INTEGRATION
from bot.integrations.google_calendar.tools import LOOKUP_TOOL
from bot.memory import NameMatch

TOOLS = "bot.integrations.google_calendar.tools"
MAYA = {"discord_id": "11", "display_name": "Maya Chen", "calendar_email": "maya@uw.edu"}
RAJ = {"discord_id": "33", "display_name": "Raj Mehta", "calendar_email": None}


def context(*known_ids):
    ctx = RunContext(session=AsyncMock(), guild_id="10", channel_id="40", discord_user_id="1")
    ctx.known_discord_ids.update(known_ids)
    return ctx


def lookup(ctx, params, *, match=NameMatch(), by_id=None):
    async def run():
        with (
            patch(f"{TOOLS}.match_user_by_name", new=AsyncMock(return_value=match)),
            patch(f"{TOOLS}.find_user_by_discord_id", new=AsyncMock(return_value=by_id)),
        ):
            return await LOOKUP_TOOL.handler(ctx, params)

    return asyncio.run(run())


def test_integration_shape():
    assert INTEGRATION.key == "google_calendar"
    assert all(a.startswith("GOOGLECALENDAR_") for a in ACTIONS)
    assert [t.name for t in INTEGRATION.local_tools] == ["lookup_calendar_email"]
    assert "never guess" in INTEGRATION.prompt.lower()
    assert "deferred_invitees" in INTEGRATION.prompt


def test_unique_match_with_an_email_is_found():
    result = lookup(context(), {"name": "Maya"}, match=NameMatch(user=MAYA))
    assert result == {"success": True, "found": True, "display_name": "Maya Chen", "email": "maya@uw.edu"}


def test_unique_match_without_an_email_hands_back_the_exact_id_to_defer_on():
    ctx = context()
    result = lookup(ctx, {"name": "Raj"}, match=NameMatch(user=RAJ))
    assert result["success"] and not result["found"]
    assert result["discord_id"] == "33" and result["display_name"] == "Raj Mehta"
    assert "deferred_invitees" in result["note"]
    assert "33" in ctx.known_discord_ids  # the model may now pass it to a calendar write


def test_match_with_no_discord_account_asks_for_an_address():
    nobody = {"discord_id": None, "display_name": "Dash Board", "calendar_email": None}
    ctx = context()
    result = lookup(ctx, {"name": "Dash"}, match=NameMatch(user=nobody))
    assert not result["found"] and "discord_id" not in result
    assert "email address" in result["note"]
    assert not ctx.known_discord_ids


def test_ambiguous_name_lists_candidates_and_authorizes_nobody():
    ctx = context()
    result = lookup(
        ctx, {"name": "Maya"}, match=NameMatch(candidates=("Maya Chen", "Maya Ortiz"), ambiguous=True)
    )
    assert result["ambiguous"] and result["candidates"] == ["Maya Chen", "Maya Ortiz"]
    assert "do not prepare the meeting yet" in result["note"].lower()
    assert "ask the requester which" in result["note"].lower()
    assert not ctx.known_discord_ids


def test_unknown_name_offers_close_names_as_suggestions_only():
    ctx = context()
    result = lookup(ctx, {"name": "Maya Chan"}, match=NameMatch(candidates=("Maya Chen",)))
    assert not result["found"] and result["suggestions"] == ["Maya Chen"]
    assert "Never guess" in result["note"] and not ctx.known_discord_ids
    plain = lookup(ctx, {"name": "Nobody"}, match=NameMatch())
    assert "suggestions" not in plain and "@mention" in plain["note"]
    # Asking comes first: no half-right proposal while the person is unknown.
    assert "do not prepare the meeting yet" in plain["note"].lower()
    assert "do not prepare the meeting yet" in result["note"].lower()


def test_discord_id_must_come_from_the_request_or_an_earlier_lookup():
    refused = lookup(context(), {"name": "Maya", "discord_id": "999"}, by_id=MAYA)
    assert refused["success"] is False and "Unknown discord_id" in refused["error"]

    ctx = context("11", "33")
    assert lookup(ctx, {"name": "Maya", "discord_id": "11"}, by_id=MAYA)["email"] == "maya@uw.edu"
    result = lookup(ctx, {"name": "Raj", "discord_id": "33"}, by_id={**RAJ, "calendar_email": None})
    assert not result["found"] and result["discord_id"] == "33" and result["display_name"] == "Raj Mehta"
    unregistered = lookup(ctx, {"name": "Newcomer", "discord_id": "33"}, by_id=None)
    assert unregistered["discord_id"] == "33" and unregistered["display_name"] == "Newcomer"


def test_a_name_is_required_when_there_is_no_id():
    assert lookup(context(), {})["success"] is False
