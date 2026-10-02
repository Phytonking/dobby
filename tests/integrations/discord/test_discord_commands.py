"""Tests for the general commands (/email, /help) in bot/integrations/discord/commands.py."""

import asyncio
from unittest.mock import AsyncMock, Mock, patch

from discord import app_commands

from test_discord_client import fake_session_local, make_bot

MOD = "bot.integrations.discord.commands"


def make_interaction(user_id=1):
    interaction = Mock()
    interaction.guild_id = 10
    interaction.user.id = user_id
    interaction.user.display_name = "Maya"
    interaction.user.roles = []
    interaction.channel_id = 40
    interaction.response.send_message = AsyncMock()
    interaction.response.defer = AsyncMock()
    interaction.edit_original_response = AsyncMock()
    return interaction


def run_email(bot, interaction, action, email=None):
    command = bot.tree.get_command("email")
    return command.callback(interaction, app_commands.Choice(name=action, value=action), email)


def test_email_set_saves_a_normalized_address():
    async def run():
        bot = make_bot()
        interaction = make_interaction()
        with (
            patch(f"{MOD}.SessionLocal") as mock_sl,
            patch(f"{MOD}.save_calendar_email", new=AsyncMock(return_value=True)) as setter,
            patch(f"{MOD}.find_user_by_discord_id", new=AsyncMock(return_value=None)),
        ):
            mock_sl.return_value = fake_session_local()
            await run_email(bot, interaction, "set", " <Maya@UW.edu> ")
        assert setter.await_args.args[1:] == ("1", "maya@uw.edu", "Maya")
        text = interaction.response.send_message.await_args.args[0]
        assert "saved" in text.lower()
        assert interaction.response.send_message.await_args.kwargs["ephemeral"] is True

    asyncio.run(run())


def test_email_set_rejects_invalid_and_registers_new_people():
    async def run():
        bot = make_bot()
        with (
            patch(f"{MOD}.SessionLocal") as mock_sl,
            patch(f"{MOD}.save_calendar_email", new=AsyncMock(return_value=True)) as setter,
            patch(f"{MOD}.find_user_by_discord_id", new=AsyncMock(return_value=None)),
        ):
            mock_sl.return_value = fake_session_local()
            interaction = make_interaction()
            await run_email(bot, interaction, "set", "not-an-email")
            setter.assert_not_awaited()
            assert "email" in interaction.response.send_message.await_args.args[0].lower()

            interaction = make_interaction()
            await run_email(bot, interaction, "set", "maya@uw.edu")
            assert "saved" in interaction.response.send_message.await_args.args[0]
            setter.assert_awaited_once()

    asyncio.run(run())


def test_email_show_masks_the_address():
    async def run():
        bot = make_bot()
        interaction = make_interaction()
        with (
            patch(f"{MOD}.SessionLocal") as mock_sl,
            patch(
                f"{MOD}.find_user_by_discord_id",
                new=AsyncMock(return_value={"display_name": "Maya", "calendar_email": "maya@uw.edu"}),
            ),
        ):
            mock_sl.return_value = fake_session_local()
            await run_email(bot, interaction, "show")
        text = interaction.response.send_message.await_args.args[0]
        assert "m***@uw.edu" in text and "maya@" not in text

    asyncio.run(run())


def test_email_denied_for_unauthorized_user():
    async def run():
        bot = make_bot()
        interaction = make_interaction(user_id=999)
        with patch(f"{MOD}.set_calendar_email", new=AsyncMock()) as setter:
            await run_email(bot, interaction, "set", "x@uw.edu")
        setter.assert_not_awaited()
        assert interaction.response.send_message.await_args.kwargs["ephemeral"] is True

    asyncio.run(run())


def test_help_lists_every_integration():
    async def run():
        bot = make_bot()
        interaction = make_interaction()
        await bot.tree.get_command("help").callback(interaction)
        text = interaction.edit_original_response.await_args.kwargs["content"]
        for label in ("Google Calendar", "Notion", "Instagram", "LinkedIn"):
            assert f"**{label}**" in text
        assert "/email" in text and "last 50 messages" in text
        assert len(text) <= 2000

    asyncio.run(run())
