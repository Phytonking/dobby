import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from unittest.mock import patch
from bot.config import Config
from bot.main import CANCEL, CONFIRM, Bot, PendingConfirmation, chunks
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


def pending_bot(mention=True, owner=1):
    bot = Mock()
    bot.user.id = 5
    bot.member_allowed = AsyncMock(return_value=True)
    bot.work = AsyncMock(return_value={"id": "abc"})
    bot.error = Bot.error
    bot.on_raw_reaction_add = Bot.on_raw_reaction_add.__get__(bot)
    bot.finish = Bot.finish.__get__(bot)
    bot.expire_after = Bot.expire_after.__get__(bot)
    bot.present = Bot.present.__get__(bot)
    bot.expiry_tasks = {}
    message = bot.get_channel.return_value.get_partial_message.return_value
    message.edit = AsyncMock()
    message.clear_reactions = AsyncMock()
    entry = PendingConfirmation(
        owner=owner,
        proposal=Proposal("create", {}, None, None, "123", None),
        channel_id=40,
        message_id=900,
        guild_id=10,
        mention=mention,
        expires=time.monotonic() + 120,
    )
    bot.confirmations = {900: entry}
    return bot, entry, message


def reaction(emoji, user=1, message=900):
    return SimpleNamespace(emoji=emoji, user_id=user, message_id=message, channel_id=40)


def test_only_owner_can_confirm_and_a_second_reaction_never_writes_again():
    async def run():
        bot, entry, message = pending_bot()
        await bot.on_raw_reaction_add(reaction(CONFIRM, user=2))
        bot.work.assert_not_awaited()
        assert not entry.used
        await asyncio.gather(
            bot.on_raw_reaction_add(reaction(CONFIRM)), bot.on_raw_reaction_add(reaction(CONFIRM))
        )
        bot.work.assert_awaited_once()
        bot.member_allowed.assert_awaited_once_with(1, 40, mention=True)
        content = message.edit.call_args.kwargs["content"]
        assert "created the meeting" in content and "abc" in content
        message.clear_reactions.assert_awaited_once()
        assert 900 not in bot.confirmations

    asyncio.run(run())


def test_red_circle_cancels_without_touching_the_calendar():
    async def run():
        bot, entry, message = pending_bot()
        await bot.on_raw_reaction_add(reaction(CANCEL))
        bot.work.assert_not_awaited()
        assert "cancelled" in message.edit.call_args.kwargs["content"]
        assert entry.used

    asyncio.run(run())


def test_other_emoji_bot_reactions_unknown_messages_and_expired_entries_are_ignored():
    async def run():
        bot, entry, message = pending_bot()
        await bot.on_raw_reaction_add(reaction("👍"))
        await bot.on_raw_reaction_add(reaction(CONFIRM, user=5))
        await bot.on_raw_reaction_add(reaction(CONFIRM, message=901))
        entry.expires = 0
        await bot.on_raw_reaction_add(reaction(CONFIRM))
        bot.work.assert_not_awaited()
        message.edit.assert_not_awaited()

    asyncio.run(run())


def test_confirmation_rechecks_current_roles_in_origin_channel():
    async def run():
        bot, entry, message = pending_bot(mention=False)
        bot.member_allowed.return_value = False
        await bot.on_raw_reaction_add(reaction(CONFIRM))
        bot.member_allowed.assert_awaited_once_with(1, 40, mention=False)
        bot.work.assert_not_awaited()
        assert "cannot use this confirmation" in message.edit.call_args.kwargs["content"]

    asyncio.run(run())


def test_apply_failure_is_reported_without_provider_details():
    async def run():
        bot, entry, message = pending_bot()
        bot.work.side_effect = RuntimeError("secret body")
        await bot.on_raw_reaction_add(reaction(CONFIRM))
        content = message.edit.call_args.kwargs["content"]
        assert "secret body" not in content
        assert "/events" in content

    asyncio.run(run())


def test_expiry_edits_the_preview_and_retires_it():
    async def run():
        bot, entry, message = pending_bot()
        with patch("bot.main.asyncio.sleep", new=AsyncMock()):
            await bot.expire_after(900)
        assert "expired" in message.edit.call_args.kwargs["content"]
        assert 900 not in bot.confirmations
        await bot.on_raw_reaction_add(reaction(CONFIRM))
        bot.work.assert_not_awaited()

    asyncio.run(run())


def test_present_puts_details_inline_and_arms_both_reactions():
    async def run():
        bot, _, _ = pending_bot()
        bot.confirmations = {}
        reply = Mock(id=77)
        reply.edit = AsyncMock()
        reply.add_reaction = AsyncMock()
        reply.channel.id = 40
        reply.channel.send = AsyncMock()
        reply.guild.id = 10
        proposal = Proposal("create", {}, None, None, "123", None)
        await bot.present(reply, "Title: Sync", 1, proposal, mention=True)
        content = reply.edit.call_args.kwargs["content"]
        assert content.startswith("Title: Sync")
        assert CONFIRM in content and CANCEL in content
        assert "attachments" not in reply.edit.call_args.kwargs
        assert [c.args[0] for c in reply.add_reaction.await_args_list] == [CONFIRM, CANCEL]
        entry = bot.confirmations[77]
        assert (entry.owner, entry.channel_id, entry.mention) == (1, 40, True)
        bot.expiry_tasks[77].cancel()

    asyncio.run(run())


def test_long_previews_continue_in_a_second_message_instead_of_a_file():
    async def run():
        bot, _, _ = pending_bot()
        bot.confirmations = {}
        reply = Mock(id=78)
        reply.edit = AsyncMock()
        reply.add_reaction = AsyncMock()
        reply.channel.id = 40
        reply.channel.send = AsyncMock()
        long = "\n".join(f"line {i} " + "x" * 80 for i in range(40))
        await bot.present(reply, long, 1, Proposal("create", {}, None, None, "1", None), mention=False)
        assert len(reply.edit.call_args.kwargs["content"]) <= 1900
        reply.channel.send.assert_awaited()
        bot.expiry_tasks[78].cancel()

    asyncio.run(run())


def test_chunks_split_on_line_boundaries():
    parts = chunks("a\n" * 1000 + "b", 1900)
    assert all(len(p) <= 1900 for p in parts)
    assert "".join(p + "\n" for p in parts).replace("\n\n", "\n").rstrip("\n") == ("a\n" * 1000 + "b").rstrip(
        "\n"
    )


def test_unauthorized_command_never_defers_or_calls_service():
    async def run():
        bot = Mock()
        bot.allowed.return_value = False
        item = interaction()
        assert not await Bot.gate(bot, item)
        item.response.defer.assert_not_awaited()
        bot.work.assert_not_called()

    asyncio.run(run())


def test_authorized_slash_reply_is_public():
    async def run():
        bot = Mock()
        bot.allowed.return_value = True
        bot.take_cooldown.return_value = True
        item = interaction()
        assert await Bot.gate(bot, item)
        item.response.defer.assert_awaited_once_with(thinking=True)

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
            assert bot.intents.guild_reactions
            await bot.close()

    asyncio.run(run())
