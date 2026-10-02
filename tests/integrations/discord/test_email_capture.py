import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from bot.integrations.discord.emails import capture_email, parse_email, recover_recent_emails
from bot.integrations.discord.client import Bot
from test_discord_client import make_bot

MOD = "bot.integrations.discord.emails"


def message(text, *, discord_id=2):
    return SimpleNamespace(
        content=text,
        author=SimpleNamespace(id=discord_id, bot=False, display_name="Leo"),
        guild=SimpleNamespace(id=10),
        channel=SimpleNamespace(id=40),
        created_at=datetime.now(timezone.utc),
        reference=None,
        mentions=[],
        reply=AsyncMock(),
    )


@pytest.mark.parametrize(
    "text,name,email",
    [
        ("Hey <@5> my name is Leonard and my email is Leonard@example.com", "Leonard", "leonard@example.com"),
        ("My email is <leo@example.com>.", "Leo", "leo@example.com"),
        ("my email address: leo@example.com", "Leo", "leo@example.com"),
        ("hi @dobby my name is Leonard Park, my email is leo@example.com", "Leonard Park", "leo@example.com"),
    ],
)
def test_explicit_self_email_forms(text, name, email):
    assert parse_email(text, "Leo") == (name, email)


@pytest.mark.parametrize(
    "text",
    [
        "Invite Leonard at leonard@example.com",
        "Leonard says my email is leo@example.com",
        "> my email is leo@example.com",
        "`my email is leo@example.com`",
        "my email is not-valid",
        "leo@example.com",
        "my email is leo@example.com\nbut this is a quote",
    ],
)
def test_does_not_treat_other_people_or_quotes_as_self_registration(text):
    assert parse_email(text, "Leo") is None


def test_on_message_saves_without_requiring_a_bot_mention_and_reconciles():
    async def run():
        bot = make_bot()
        msg = message("My name is Leonard and my email is leo@example.com")
        with (
            patch.object(bot, "member_allowed", new=AsyncMock(return_value=True)),
            patch(f"{MOD}.SessionLocal", return_value=AsyncMock()),
            patch(f"{MOD}.save_calendar_email", new=AsyncMock(return_value=True)) as save,
        ):
            await Bot.on_message(bot, msg)
        assert save.await_args.args[1:] == ("2", "leo@example.com", "Leonard")
        assert save.await_args.kwargs["observed_at"] == msg.created_at
        assert "saved" in msg.reply.await_args.args[0]
        bot.reconcile_calendar_invites.assert_awaited_once()
        bot.agent.run.assert_not_awaited()

    asyncio.run(run())


@pytest.mark.parametrize("original_author", [5, 99])
@pytest.mark.parametrize("reply", ["leo@example.com", "Leonard: leo@example.com"])
def test_bare_email_must_reply_to_dobbys_email_prompt(original_author, reply):
    async def run():
        bot = make_bot()
        msg = message(reply)
        msg.reference = SimpleNamespace(message_id=123)
        msg.channel.fetch_message = AsyncMock(
            return_value=SimpleNamespace(
                author=SimpleNamespace(id=original_author), content="Please reply with your email"
            )
        )
        with (
            patch.object(bot, "member_allowed", new=AsyncMock(return_value=True)),
            patch(f"{MOD}.SessionLocal", return_value=AsyncMock()),
            patch(f"{MOD}.save_calendar_email", new=AsyncMock(return_value=True)) as save,
        ):
            handled = await capture_email(bot, msg)
        assert handled is (original_author == 5)
        assert save.await_count == (1 if original_author == 5 else 0)

    asyncio.run(run())


def test_unauthorized_self_email_is_not_saved():
    async def run():
        bot = make_bot()
        with (
            patch.object(bot, "member_allowed", new=AsyncMock(return_value=False)),
            patch(f"{MOD}.save_calendar_email", new=AsyncMock()) as save,
        ):
            assert not await capture_email(bot, message("my email is leo@example.com"))
        save.assert_not_awaited()

    asyncio.run(run())


@pytest.mark.parametrize("limit,channel_id,expected", [(2, 40, 2), (0, 40, 0), (2, 99, 0)])
def test_history_capture_uses_configured_window_and_channels(limit, channel_id, expected):
    async def run():
        bot = make_bot()
        bot.config.context_limit = limit
        rows = [message("my email is leo@example.com") for _ in range(4)]

        async def history(**kwargs):
            assert kwargs["limit"] == limit * 4
            for row in rows:
                yield row

        channel = SimpleNamespace(id=channel_id, history=history)
        with patch(f"{MOD}.capture_email", new=AsyncMock()) as capture:
            await recover_recent_emails(bot, channel)
        assert capture.await_count == expected
        if expected:
            assert capture.await_args.kwargs == {"history": True}

    asyncio.run(run())
