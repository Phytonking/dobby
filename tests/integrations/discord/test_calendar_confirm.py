import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import discord
import pytest

from bot.agent import AgentResult
from bot.integrations.base import PendingAction
from bot.integrations.discord.calendar_confirm import CalendarConfirmation, present_calendar
from bot.integrations.discord.client import Bot

MOD = "bot.integrations.discord.calendar_confirm"


def setup():
    message = Mock(id=100, channel=Mock(id=40), guild=Mock(id=10))
    message.edit = AsyncMock(return_value=message)
    message.add_reaction = AsyncMock()
    message.clear_reactions = AsyncMock()
    message.channel.send = AsyncMock(return_value=message)
    bot = SimpleNamespace(calendar_confirmations={}, member_allowed=AsyncMock(return_value=True))
    action = PendingAction(
        "google_calendar",
        "Create meeting",
        "**Title:** Review",
        AsyncMock(return_value={"success": True, "message": "Meeting created"}),
    )
    entry = CalendarConfirmation(bot, message, [action], 1, mention=True)
    return bot, message, action, entry


def payload(emoji="🟢", user=1):
    return SimpleNamespace(emoji=emoji, user_id=user, channel_id=40, guild_id=10, message_id=100)


def test_confirm_only_once_and_audits():
    async def run():
        bot, message, action, entry = setup()
        await entry.arm()
        await entry.react(payload(user=2))
        await entry.react(payload(emoji="✅"))
        action.execute.assert_not_awaited()
        with (
            patch(f"{MOD}.SessionLocal", return_value=AsyncMock()),
            patch(f"{MOD}.record_action", new=AsyncMock()) as audit,
        ):
            await asyncio.gather(entry.react(payload()), entry.react(payload()))
        action.execute.assert_awaited_once()
        assert audit.await_args.kwargs["status"] == "ok"
        bot.member_allowed.assert_awaited_once_with(1, 40, mention=True)
        assert not bot.calendar_confirmations
        assert message.edit.await_args.kwargs["content"] == "Meeting created"

    asyncio.run(run())


@pytest.mark.parametrize("case", ["cancel", "timeout", "expired", "revoked"])
def test_non_confirm_paths_never_execute(case):
    async def run():
        bot, message, action, entry = setup()
        await entry.arm()
        if case == "cancel":
            await entry.react(payload(emoji="🔴"))
        elif case == "revoked":
            bot.member_allowed.return_value = False
            await entry.react(payload())
        elif case == "expired":
            entry.expires = 0
            await entry.react(payload())
        else:
            with patch(f"{MOD}.asyncio.sleep", new=AsyncMock()):
                await entry.expire()
        action.execute.assert_not_awaited()
        entry.task.cancel()

    asyncio.run(run())


@pytest.mark.parametrize("slash", [False, True])
def test_client_routes_calendar_to_original_template_and_reactions(slash):
    async def run():
        bot, message, action, entry = setup()
        if slash:
            target = Mock(spec=discord.Interaction)
            target.edit_original_response = AsyncMock(return_value=message)
            edit = target.edit_original_response
        else:
            target, edit = message, message.edit
        await Bot.send_result(bot, target, AgentResult("Already done!", [action]), 1)
        content = edit.await_args.kwargs["content"]
        assert "Already done" not in content
        assert "**Create meeting**\n**Title:** Review" in content
        assert "2 minutes" in content and "🟢" in content and "🔴" in content
        assert edit.await_args.kwargs["view"] is None
        assert [c.args[0] for c in message.add_reaction.await_args_list] == ["🟢", "🔴"]
        entry = bot.calendar_confirmations[message.id]
        assert entry.mention is not slash
        entry.task.cancel()

    asyncio.run(run())


def test_long_preview_is_not_truncated():
    async def run():
        bot, message, action, entry = setup()
        action = PendingAction("google_calendar", "Create meeting", "x" * 4500 + "THE END", action.execute)
        await present_calendar(bot, message, [action], 1)
        parts = [message.edit.await_args.kwargs["content"]]
        parts.extend(c.args[0] for c in message.channel.send.await_args_list)
        assert all(len(part) <= 2000 for part in parts)
        assert "x" * 4500 + "THE END" in "".join(parts)
        bot.calendar_confirmations[message.id].task.cancel()

    asyncio.run(run())


def test_cannot_add_reactions_leaves_no_executable_proposal():
    async def run():
        bot, message, action, entry = setup()
        message.add_reaction.side_effect = discord.Forbidden(Mock(status=403, reason="Forbidden"), "denied")
        await entry.arm()
        assert not bot.calendar_confirmations
        action.execute.assert_not_awaited()

    asyncio.run(run())


@pytest.mark.parametrize("raises", [False, True])
def test_failed_write_is_retired_and_audited(raises):
    async def run():
        bot, message, action, entry = setup()
        action.execute.return_value = {"success": False}
        if raises:
            action.execute.side_effect = RuntimeError("provider unavailable")
        await entry.arm()
        with (
            patch(f"{MOD}.SessionLocal", return_value=AsyncMock()),
            patch(f"{MOD}.record_action", new=AsyncMock()) as audit,
        ):
            await entry.react(payload())
        assert audit.await_args.kwargs["status"] == "error"
        assert "could not complete" in message.edit.await_args.kwargs["content"]
        assert not bot.calendar_confirmations
        await entry.react(payload())
        action.execute.assert_awaited_once()

    asyncio.run(run())


def test_raw_reaction_handler_routes_only_registered_messages():
    async def run():
        bot, message, action, entry = setup()
        bot.user = SimpleNamespace(id=99)
        entry.react = AsyncMock()
        await Bot.on_raw_reaction_add(bot, payload())
        entry.react.assert_not_awaited()
        bot.calendar_confirmations[100] = entry
        await Bot.on_raw_reaction_add(bot, payload(user=99))
        entry.react.assert_not_awaited()
        await Bot.on_raw_reaction_add(bot, payload())
        entry.react.assert_awaited_once()

    asyncio.run(run())
