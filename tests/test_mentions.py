import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from bot.main import Bot
from bot.service import Proposal


def setup():
    bot = Mock()
    bot.config.guild = 10
    bot.config.mention_channels = {40}
    bot.config.context_limit = 6
    bot.user.id = 5
    bot.member_allowed = AsyncMock(return_value=True)
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
    message.author.send.return_value.edit = AsyncMock()
    message.guild.id = 10
    message.channel.id = 40
    message.id = 100
    message.mentions = [bot.user]
    message.content = "<@5> make this a meeting"
    message.reply = AsyncMock()
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
        assert [row["text"] for row in args[-1]] == ["earlier", "latest"]
        message.author.send.return_value.edit.assert_awaited_once()
        message.reply.assert_not_awaited()

    asyncio.run(run())


def test_complete_mention_does_not_fetch_history():
    async def run():
        bot, message = setup()
        message.content = "<@5> schedule Planning tomorrow at 10am"
        await Bot.on_message(bot, message)
        message.channel.history.assert_not_called()
        assert bot.work.call_args.args[-1] == []

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
