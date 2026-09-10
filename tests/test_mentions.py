import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, call
import discord
import pytest
from bot.config import Config
from bot.main import Bot
from bot.service import Proposal


def setup(mention_channels=frozenset({40})):
    bot = Mock()
    bot.config.guild = 10
    bot.config.mention_channels = mention_channels
    bot.config.channels = frozenset()
    # Bind the real predicate so the tests exercise the shipped gating rule.
    bot.config.mentionable = Config.mentionable.__get__(bot.config)
    bot.config.context_limit = 6
    bot.config.timezone = "UTC"
    bot.user.id = 5
    bot.member_allowed = AsyncMock(return_value=True)
    bot.mention_reply = Bot.mention_reply.__get__(bot)
    bot.gather_history = Bot.gather_history.__get__(bot)
    bot.pending_followup = Bot.pending_followup.__get__(bot)
    bot.handle_request = Bot.handle_request.__get__(bot)
    bot.ask = Bot.ask.__get__(bot)
    bot.awaiting = {}
    bot.contacts = Mock()
    bot.converse = AsyncMock(return_value=("schedule", None))
    bot.present = AsyncMock()
    bot.take_cooldown.return_value = True
    bot.work = AsyncMock(
        return_value=Proposal(
            "create",
            {
                "summary": "Sync",
                "start": {"dateTime": "2030-01-01T10:00:00Z"},
                "end": {"dateTime": "2030-01-01T11:00:00Z"},
            },
            None,
            None,
            "id",
            None,
        )
    )
    message = Mock()
    message.author.bot = False
    message.author.id = 1
    message.author.send = AsyncMock()
    message.reply.return_value.edit = AsyncMock()
    message.guild.id = 10
    message.channel.id = 40
    message.channel.name = "general"

    async def no_history(**kwargs):
        return
        yield

    message.channel.history = Mock(side_effect=no_history)
    message.id = 100
    message.mentions = [bot.user]
    message.reference = None
    message.content = "<@5> make this a meeting"
    message.reply = AsyncMock()
    message.reply.return_value.edit = AsyncMock()
    return bot, message


def test_unauthorized_mention_never_reads_history_or_calls_ai():
    async def run():
        bot, message = setup()
        bot.member_allowed.return_value = False
        await Bot.on_message(bot, message)
        message.channel.history.assert_not_called()
        message.author.send.assert_not_awaited()
        bot.work.assert_not_awaited()

    asyncio.run(run())


def test_context_is_bounded_same_channel_and_excludes_bots():
    async def run():
        bot, message = setup()

        async def history(**kwargs):
            for text, is_bot in [("latest", False), ("ignore bot", True), ("earlier", False)]:
                yield SimpleNamespace(
                    content=text,
                    author=SimpleNamespace(bot=is_bot),
                    created_at=SimpleNamespace(isoformat=lambda: "2030-01-01T00:00:00Z"),
                )

        message.channel.history = Mock(side_effect=history)
        await Bot.on_message(bot, message)
        message.channel.history.assert_called_once_with(limit=6, before=message)
        args = bot.work.call_args.args
        assert [row["text"] for row in args[4]] == ["earlier", "latest"]
        assert all("author" in row for row in args[4])
        assert args[5] == {"channel": "general", "thread": None}
        bot.present.assert_awaited_once()
        message.reply.assert_awaited_once()

    asyncio.run(run())


def test_complete_mention_still_gathers_context_before_planning():
    async def run():
        bot, message = setup()
        message.content = "<@5> schedule Planning tomorrow at 10am"
        await Bot.on_message(bot, message)
        message.channel.history.assert_called_once_with(limit=6, before=message)
        assert bot.work.call_args.args[4] == []
        assert bot.work.call_args.args[5]["channel"] == "general"
        message.author.send.assert_not_awaited()
        message.reply.assert_awaited_once()
        assert message.reply.call_args.kwargs["mention_author"] is False
        reply, content, owner, proposal = bot.present.await_args.args
        assert reply is message.reply.return_value
        assert "Sync" in content
        assert owner == message.author.id
        assert bot.present.await_args.kwargs == {"mention": True}

    asyncio.run(run())


def test_no_dm_fallback_when_thread_send_is_forbidden():
    async def run():
        bot, message = setup()
        message.reply.side_effect = discord.Forbidden(Mock(status=403, reason="Forbidden"), "no access")
        await Bot.on_message(bot, message)
        message.author.send.assert_not_awaited()
        bot.work.assert_not_awaited()

    asyncio.run(run())


def test_planning_error_updates_channel_reply():
    async def run():
        bot, message = setup()
        bot.error = Bot.error
        message.content = "<@5> schedule Sync tomorrow at 10am"
        bot.work.side_effect = RuntimeError("provider private data")
        await Bot.on_message(bot, message)
        content = message.reply.return_value.edit.call_args.kwargs["content"]
        assert "could not complete" in content
        assert "provider private data" not in content
        message.author.send.assert_not_awaited()

    asyncio.run(run())


def test_bare_mention_replies_without_calling_ai():
    async def run():
        bot, message = setup()
        message.content = "<@!5>"
        bot.error = Bot.error
        await Bot.on_message(bot, message)
        bot.work.assert_not_awaited()
        assert "meeting request" in message.reply.return_value.edit.call_args.kwargs["content"]
        message.reply.assert_awaited_once()

    asyncio.run(run())


def test_cooldown_gets_visible_feedback():
    async def run():
        bot, message = setup()
        bot.take_cooldown.return_value = False
        await Bot.on_message(bot, message)
        message.reply.assert_awaited_once()
        bot.work.assert_not_awaited()

    asyncio.run(run())


def test_requester_without_history_permission_gets_no_context_but_still_schedules():
    async def run():
        bot, message = setup()
        message.channel.permissions_for.return_value.read_message_history = False
        await Bot.on_message(bot, message)
        message.channel.history.assert_not_called()
        bot.work.assert_awaited_once()
        assert bot.work.call_args.args[4] == []


def test_thread_place_includes_parent_channel_name():
    from bot.main import describe_place

    thread = Mock(spec=discord.Thread)
    thread.name = "Q4 launch prep"
    thread.parent = Mock()
    thread.parent.name = "planning"
    assert describe_place(thread) == {"channel": "planning", "thread": "Q4 launch prep"}


def test_member_without_history_permission_can_schedule():
    async def run():
        bot, _ = setup()
        guild = bot.get_guild.return_value
        guild.id = 10
        member = Mock(id=1, roles=[])
        guild.fetch_member = AsyncMock(return_value=member)
        permissions = guild.get_channel_or_thread.return_value.permissions_for.return_value
        permissions.view_channel = True
        permissions.read_message_history = False
        bot.config.allows.return_value = True
        assert await Bot.member_allowed(bot, 1, 40)

    asyncio.run(run())


@pytest.mark.parametrize("cached", [True, False])
def test_thread_or_uncached_channel_authorizes_like_slash_command(cached):
    async def run():
        bot, _ = setup()
        bot.config = Config(
            "fake",
            10,
            frozenset(),
            frozenset({7}),
            frozenset({40}),
            "fake",
            "test",
            "unused",
            "primary",
            "UTC",
        )
        guild = bot.get_guild.return_value
        guild.id = 10
        member = Mock(id=1, roles=[Mock(id=7)])
        guild.fetch_member = AsyncMock(return_value=member)
        channel = Mock(spec=discord.Thread)
        channel.is_private.return_value = False
        channel.permissions_for.return_value.view_channel = True
        guild.get_channel.return_value = None
        guild.get_channel_or_thread.return_value = channel if cached else None
        guild.fetch_channel = AsyncMock(return_value=channel)
        interaction = Mock(guild_id=10, user=member, channel_id=40)
        assert Bot.allowed(bot, interaction)
        assert await Bot.member_allowed(bot, 1, 40)
        assert guild.fetch_channel.await_count == (0 if cached else 1)
        guild.get_channel.assert_not_called()

    asyncio.run(run())


def test_private_thread_membership_is_rechecked_before_authorizing(caplog):
    async def run():
        bot, _ = setup()
        guild = bot.get_guild.return_value
        guild.id = 10
        guild.fetch_member = AsyncMock(return_value=Mock(id=1, roles=[]))
        channel = Mock(spec=discord.Thread)
        channel.is_private.return_value = True
        channel.permissions_for.return_value.view_channel = True
        channel.permissions_for.return_value.manage_threads = False
        channel.fetch_member = AsyncMock(
            side_effect=discord.NotFound(Mock(status=404, reason="Not Found"), "private data")
        )
        guild.get_channel_or_thread.return_value = channel
        assert not await Bot.member_allowed(bot, 1, 40)
        channel.fetch_member.assert_awaited_once_with(1)
        assert "discord_lookup_failed" in caplog.text
        assert "private data" not in caplog.text

    asyncio.run(run())


@pytest.mark.parametrize("denial", ["channel", "role", "visibility"])
def test_member_lookup_preserves_restrictions_and_logs_reason(denial, caplog):
    async def run():
        bot, _ = setup()
        bot.config = Config(
            "fake",
            10,
            frozenset(),
            frozenset({7}),
            frozenset({99 if denial == "channel" else 40}),
            "fake",
            "test",
            "unused",
            "primary",
            "UTC",
        )
        guild = bot.get_guild.return_value
        guild.id = 10
        guild.fetch_member = AsyncMock(
            return_value=Mock(id=1, roles=[] if denial == "role" else [Mock(id=7)])
        )
        guild.get_channel_or_thread.return_value.permissions_for.return_value.view_channel = (
            denial != "visibility"
        )
        assert not await Bot.member_allowed(bot, 1, 40)
        expected = {
            "channel": "channel_not_in_ALLOWED_CHANNEL_IDS",
            "role": "user_or_roles_not_in_allowlist",
            "visibility": "requester_cannot_view_channel",
        }
        assert expected[denial] in caplog.text

    asyncio.run(run())


def test_non_allowlisted_channel_and_non_mention_are_ignored():
    async def run():
        bot, message = setup()
        message.channel.id = 41
        await Bot.on_message(bot, message)
        bot.member_allowed.assert_not_awaited()
        message.channel.id = 40
        message.mentions = []
        await Bot.on_message(bot, message)
        bot.work.assert_not_awaited()

    asyncio.run(run())


def test_empty_mention_channels_allows_every_channel():
    """Empty MENTION_CHANNEL_IDS means any channel, like an empty ALLOWED_CHANNEL_IDS."""

    async def run():
        for channel_id in (40, 41, 999):
            bot, message = setup(mention_channels=frozenset())
            message.channel.id = channel_id
            message.content = "<@5> schedule Planning tomorrow at 10am"
            await Bot.on_message(bot, message)
            # Checked once to admit the mention, once again before the channel preview.
            assert bot.member_allowed.await_args_list == [call(1, channel_id)] * 2
            bot.work.assert_awaited_once()
            message.author.send.assert_not_awaited()
            message.reply.assert_awaited_once()

    asyncio.run(run())


def test_empty_mention_channels_still_enforces_membership_and_guild():
    async def run():
        bot, message = setup(mention_channels=frozenset())
        bot.member_allowed.return_value = False
        await Bot.on_message(bot, message)
        bot.work.assert_not_awaited()
        message.author.send.assert_not_awaited()

        bot, message = setup(mention_channels=frozenset())
        message.guild.id = 11
        await Bot.on_message(bot, message)
        bot.member_allowed.assert_not_awaited()

    asyncio.run(run())


def test_unknown_invitee_asks_for_email_then_reply_saves_and_resumes():
    from bot.service import NeedContacts

    async def run():
        bot, message = setup()
        message.content = "<@5> schedule Sync tomorrow at 10am and invite Maya"
        prompt = message.reply.return_value
        prompt.id = 555
        bot.work.side_effect = [NeedContacts(["Maya"]), bot.work.return_value]
        await Bot.on_message(bot, message)
        assert "Maya" in prompt.edit.call_args.kwargs["content"]
        followup = bot.awaiting[(40, 1)]
        assert followup.names == ["Maya"] and followup.prompt_id == 555
        bot.present.assert_not_awaited()

        answer = Mock()
        answer.author = message.author
        answer.guild = message.guild
        answer.channel = message.channel
        answer.mentions = []
        answer.content = "maya@example.com"
        answer.reference = Mock(message_id=555)
        answer.reply = AsyncMock()
        answer.reply.return_value.edit = AsyncMock()
        answer.id = 101
        await Bot.on_message(bot, answer)
        bot.contacts.save.assert_called_once_with("Maya", "maya@example.com", added_by=1)
        assert (40, 1) not in bot.awaiting
        # The original request is re-run with the same context, not the email reply text.
        args = bot.work.call_args.args
        assert args[1] == "schedule Sync tomorrow at 10am and invite Maya"
        bot.present.assert_awaited_once()
        bot.take_cooldown.assert_called_once()

    asyncio.run(run())


def test_unparseable_email_reply_keeps_waiting():
    import time
    from bot.main import Followup

    async def run():
        bot, message = setup()
        bot.awaiting[(40, 1)] = Followup("emails", 555, "req", [], {}, time.monotonic() + 60, ["Maya"])
        message.mentions = []
        message.content = "no idea"
        message.reference = Mock(message_id=555)
        await Bot.on_message(bot, message)
        bot.contacts.save.assert_not_called()
        assert (40, 1) in bot.awaiting
        assert "email" in message.reply.call_args.args[0]
        bot.work.assert_not_awaited()

    asyncio.run(run())


def test_reply_to_an_unrelated_message_is_ignored_and_expired_followups_are_dropped():
    import time
    from bot.main import Followup

    async def run():
        bot, message = setup()
        bot.awaiting[(40, 1)] = Followup("emails", 555, "req", [], {}, time.monotonic() + 60, ["Maya"])
        message.mentions = []
        message.reference = Mock(message_id=999)
        await Bot.on_message(bot, message)
        bot.work.assert_not_awaited()
        bot.awaiting[(40, 1)].expires = 0
        message.reference = Mock(message_id=555)
        await Bot.on_message(bot, message)
        assert (40, 1) not in bot.awaiting
        bot.work.assert_not_awaited()

    asyncio.run(run())


def answer_to(message, prompt_id, content):
    answer = Mock()
    answer.author = message.author
    answer.guild = message.guild
    answer.channel = message.channel
    answer.mentions = []
    answer.content = content
    answer.reference = Mock(message_id=prompt_id)
    answer.reply = AsyncMock()
    answer.reply.return_value.edit = AsyncMock()
    answer.id = 202
    return answer


def test_delete_without_a_known_meeting_asks_for_title_then_searches_with_the_reply():
    from bot.service import NeedTitle

    async def run():
        bot, message = setup()
        message.content = "<@5> delete that meeting"
        prompt = message.reply.return_value
        prompt.id = 700
        bot.work.side_effect = [NeedTitle(), bot.work.return_value]
        await Bot.on_message(bot, message)
        assert "title" in prompt.edit.call_args.kwargs["content"]
        assert bot.awaiting[(40, 1)].kind == "title"

        await Bot.on_message(bot, answer_to(message, 700, "design review"))
        args = bot.work.call_args.args
        assert args[1] == "delete that meeting"
        assert args[6] == "design review"
        assert (40, 1) not in bot.awaiting
        bot.present.assert_awaited_once()

    asyncio.run(run())


def test_ambiguous_title_lists_candidates_and_a_number_reply_selects_one():
    from bot.service import ChooseEvent

    async def run():
        bot, message = setup()
        message.content = "<@5> delete the sync"
        prompt = message.reply.return_value
        prompt.id = 701
        candidates = [
            {"id": "first", "summary": "Sync A", "start": {"dateTime": "2030-01-01T10:00:00Z"}},
            {"id": "second", "summary": "Sync B", "start": {"dateTime": "2030-01-02T10:00:00Z"}},
        ]
        bot.work.side_effect = [ChooseEvent("sync", candidates), bot.work.return_value]
        await Bot.on_message(bot, message)
        listing = prompt.edit.call_args.kwargs["content"]
        assert "1. Sync A" in listing and "2. Sync B" in listing
        assert bot.awaiting[(40, 1)].candidates == ["first", "second"]

        await Bot.on_message(bot, answer_to(message, 701, "9"))
        assert bot.work.await_count == 1
        assert (40, 1) in bot.awaiting

        await Bot.on_message(bot, answer_to(message, 701, "2"))
        assert bot.work.call_args.args[2] == "second"
        assert (40, 1) not in bot.awaiting
        bot.present.assert_awaited_once()

    asyncio.run(run())


def test_questions_about_the_bot_or_off_topic_chat_are_answered_without_planning():
    async def run():
        bot, message = setup()
        message.content = "<@5> what can you do?"
        bot.converse.return_value = ("capabilities", "Dobby can do things.")
        await Bot.on_message(bot, message)
        bot.converse.assert_awaited_once()
        assert bot.converse.await_args.args[0] == "what can you do?"
        bot.work.assert_not_awaited()
        assert message.reply.return_value.edit.call_args.kwargs["content"] == "Dobby can do things."
        assert bot.awaiting == {}

    asyncio.run(run())


def test_resumed_requests_skip_the_chat_check():
    import time
    from bot.main import Followup

    async def run():
        bot, message = setup()
        bot.awaiting[(40, 1)] = Followup("title", 555, "delete it", [], {}, time.monotonic() + 60)
        message.mentions = []
        message.content = "design review"
        message.reference = Mock(message_id=555)
        await Bot.on_message(bot, message)
        bot.converse.assert_not_awaited()
        bot.work.assert_awaited_once()

    asyncio.run(run())
