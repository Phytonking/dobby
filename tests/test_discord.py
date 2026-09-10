import asyncio
from unittest.mock import AsyncMock, Mock
from unittest.mock import patch
from bot.config import Config
from bot.main import Confirmation, Bot
from bot.service import Proposal


def interaction(user=1):
    obj = Mock()
    obj.user.id = user
    obj.guild_id = 10
    obj.response.defer = AsyncMock()
    obj.response.send_message = AsyncMock()
    obj.response.is_done.return_value = False
    obj.followup.send = AsyncMock()
    obj.edit_original_response = AsyncMock()
    return obj


def test_confirmation_owner_expiry_and_reauthorization():
    async def run():
        bot = Mock()
        bot.allowed.return_value = True
        view = Confirmation(bot, 1, None)
        assert not await view.interaction_check(interaction(2))
        assert await view.interaction_check(interaction(1))
        bot.allowed.return_value = False
        assert not await view.interaction_check(interaction(1))
        bot.allowed.return_value = True
        view.expires = 0
        assert not await view.interaction_check(interaction(1))

    asyncio.run(run())


def test_dm_confirmation_checks_current_roles_in_origin_channel():
    async def run():
        bot = Mock()
        bot.member_allowed = AsyncMock(return_value=False)
        view = Confirmation(bot, 1, None, origin_channel=40)
        item = interaction()
        assert not await view.interaction_check(item)
        bot.member_allowed.assert_awaited_once_with(1, 40)
        item.response.defer.assert_awaited_once()

    asyncio.run(run())


def test_double_click_only_writes_once():
    async def run():
        bot = Mock()
        bot.work = AsyncMock(return_value={"id": "abc"})
        proposal = Proposal("create", {}, None, None, "123", None)
        view = Confirmation(bot, 1, proposal)
        await asyncio.gather(view.confirm.callback(interaction()), view.confirm.callback(interaction()))
        bot.work.assert_awaited_once()

    asyncio.run(run())


def test_unauthorized_command_never_defers_or_calls_service():
    async def run():
        bot = Mock()
        bot.allowed.return_value = False
        item = interaction()
        assert not await Bot.gate(bot, item)
        item.response.defer.assert_not_awaited()
        bot.work.assert_not_called()

    asyncio.run(run())


def test_real_discord_command_registration_without_network():
    async def run():
        config = Config(
            "fake", 10, frozenset({1}), frozenset(), frozenset(), "fake", "test", "unused", "primary", "UTC"
        )
        with patch("bot.main.Calendar"), patch("bot.main.Planner"):
            bot = Bot(config)
            assert {c.name for c in bot.tree.get_commands()} == {"schedule", "events", "calendar_help"}
            # Mentions are permitted in any channel when MENTION_CHANNEL_IDS is empty,
            # so the content intent is required regardless of that setting.
            assert bot.intents.message_content
            await bot.close()

    asyncio.run(run())
