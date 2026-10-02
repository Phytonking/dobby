"""Tests for the Confirm/Cancel gate (bot/integrations/discord/confirm.py)."""

import asyncio
from unittest.mock import AsyncMock, Mock, patch

from bot.integrations.base import PendingAction
from bot.integrations.discord.confirm import ConfirmView, preview_text

MOD = "bot.integrations.discord.confirm"


def make_pending(label="LinkedIn post", result=None, error=None):
    execute = AsyncMock(return_value=result or {"success": True})
    if error:
        execute.side_effect = error
    return PendingAction("linkedin", label, "hello world", execute)


def make_click(user_id=1):
    interaction = Mock()
    interaction.user.id = user_id
    interaction.response.send_message = AsyncMock()
    interaction.response.edit_message = AsyncMock()
    interaction.edit_original_response = AsyncMock()
    return interaction


def session_local():
    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=AsyncMock(commit=AsyncMock()))
    ctx.__aexit__ = AsyncMock(return_value=None)
    return ctx


def test_preview_text_shows_each_label_and_body():
    text = preview_text([make_pending(), make_pending(label="Instagram story")])
    assert "**LinkedIn post**\nhello world" in text and "**Instagram story**" in text
    assert "Confirm" in text


def test_only_the_requester_may_press_buttons():
    async def run():
        view = ConfirmView([make_pending()], requester_id=1, guild_id="10", channel_id="40")
        stranger = make_click(user_id=2)
        assert await view.interaction_check(stranger) is False
        stranger.response.send_message.assert_awaited_once()
        assert stranger.response.send_message.await_args.kwargs["ephemeral"] is True
        assert await view.interaction_check(make_click(user_id=1)) is True

    asyncio.run(run())


def test_confirm_executes_records_and_reports():
    async def run():
        pending = make_pending()
        view = ConfirmView([pending], requester_id=1, guild_id="10", channel_id="40")
        click = make_click()
        with (
            patch(f"{MOD}.SessionLocal", return_value=session_local()),
            patch(f"{MOD}.record_action", new=AsyncMock()) as record,
        ):
            await view.confirm.callback(click)
        pending.execute.assert_awaited_once()
        kwargs = record.await_args.kwargs
        assert kwargs["tool"] == "linkedin.publish" and kwargs["status"] == "ok"
        assert kwargs["discord_id"] == "1" and kwargs["guild_id"] == "10"
        final = click.edit_original_response.await_args.kwargs["content"]
        assert "LinkedIn post" in final and "published" in final.lower()
        assert all(child.disabled for child in view.children)
        assert view.is_finished()

    asyncio.run(run())


def test_confirm_reports_failures_and_exceptions_without_raising():
    async def run():
        failing = make_pending(label="Instagram post", result={"success": False, "error": "denied"})
        exploding = make_pending(label="Instagram story", error=RuntimeError("x"))
        view = ConfirmView([failing, exploding], requester_id=1, guild_id="10", channel_id="40")
        click = make_click()
        with (
            patch(f"{MOD}.SessionLocal", return_value=session_local()),
            patch(f"{MOD}.record_action", new=AsyncMock()) as record,
        ):
            await view.confirm.callback(click)
        assert [c.kwargs["status"] for c in record.await_args_list] == ["error", "error"]
        final = click.edit_original_response.await_args.kwargs["content"]
        assert "Instagram post" in final and "Instagram story" in final
        assert "published the" not in final.lower()

    asyncio.run(run())


def test_cancel_and_timeout_never_execute():
    async def run():
        pending = make_pending()
        view = ConfirmView([pending], requester_id=1, guild_id="10", channel_id="40")
        click = make_click()
        await view.cancel.callback(click)
        pending.execute.assert_not_awaited()
        assert "discarded" in click.response.edit_message.await_args.kwargs["content"].lower()
        assert view.is_finished()

        view = ConfirmView([pending], requester_id=1, guild_id="10", channel_id="40")
        view.message = Mock(edit=AsyncMock())
        await view.on_timeout()
        pending.execute.assert_not_awaited()
        assert "2 minutes" in view.message.edit.await_args.kwargs["content"]
        assert all(child.disabled for child in view.children)

    asyncio.run(run())
