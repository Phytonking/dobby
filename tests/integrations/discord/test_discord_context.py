"""Tests for bot/integrations/discord/context.py — live channel context and @mention resolution."""

import asyncio
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import discord

from bot.integrations.discord.context import format_message, gather_context, resolve_mentions

MOD = "bot.integrations.discord.context"


def make_message(text, *, bot=False, name="Maya", minute=0):
    return SimpleNamespace(
        author=SimpleNamespace(bot=bot, display_name=name),
        clean_content=text,
        created_at=datetime(2026, 9, 17, 10, minute),
    )


class FakeChannel:
    """channel.history() yields newest first, like discord.py."""

    def __init__(self, messages, error=None):
        self.id = 40
        self.messages = messages
        self.error = error
        self.calls = []

    def history(self, **kwargs):
        self.calls.append(kwargs)
        messages, error = self.messages, self.error

        async def gen():
            if error:
                raise error
            for m in messages:
                yield m

        return gen()


def test_gather_context_skips_bots_honors_limit_and_returns_oldest_first():
    newest_first = [
        make_message("three", minute=3),
        make_message("bot noise", bot=True, name="Dobby", minute=2),
        make_message("two", minute=2, name="Leo"),
        make_message("one", minute=1),
        make_message("zero", minute=0),
    ]
    channel = FakeChannel(newest_first)
    lines = asyncio.run(gather_context(channel, limit=3))
    assert lines == [
        "[2026-09-17 10:01] Maya: one",
        "[2026-09-17 10:02] Leo: two",
        "[2026-09-17 10:03] Maya: three",
    ]
    assert channel.calls == [{"limit": 12, "before": None}]


def test_gather_context_passes_before_and_truncates_long_messages():
    trigger = object()
    channel = FakeChannel([make_message("x" * 900)])
    lines = asyncio.run(gather_context(channel, limit=50, before=trigger))
    assert channel.calls[0]["before"] is trigger
    assert len(lines) == 1 and lines[0].endswith("x" * 500) and len(lines[0]) < 600


def test_gather_context_returns_empty_when_history_is_forbidden():
    error = discord.HTTPException(SimpleNamespace(status=403, reason="Forbidden"), "Missing Access")
    assert asyncio.run(gather_context(FakeChannel([], error=error), limit=50)) == []


def test_format_message_collapses_whitespace():
    assert format_message(make_message("  a \n b  ")) == "[2026-09-17 10:00] Maya: a b"


def test_resolve_mentions_replaces_known_ids_and_collects_people():
    maya = {"display_name": "Maya Chen", "calendar_email": "maya@uw.edu"}

    async def lookup(session, discord_id):
        return maya if discord_id == "1" else None

    async def run():
        with patch(f"{MOD}.find_user_by_discord_id", new=AsyncMock(side_effect=lookup)) as find:
            text, people = await resolve_mentions(None, "invite <@1> and <@!1> and <@2>")
            assert text == "invite @Maya Chen and @Maya Chen and <@2>"
            assert people == [{**maya, "discord_id": "1"}]
            assert find.await_count == 2

    asyncio.run(run())


def test_resolve_mentions_without_mentions_is_a_no_op():
    async def run():
        with patch(f"{MOD}.find_user_by_discord_id", new=AsyncMock()) as find:
            assert await resolve_mentions(None, "plain text") == ("plain text", [])
            find.assert_not_awaited()

    asyncio.run(run())
