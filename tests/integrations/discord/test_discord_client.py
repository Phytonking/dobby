"""Tests for the Discord client (bot/integrations/discord/client.py): gating, cooldowns, mentions, results."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import discord
import pytest

from bot.agent import AgentResult
from bot.config import Config
from bot.integrations import ToolRegistry
from bot.integrations.base import PendingAction
from bot.integrations.discord.client import Bot
from bot.models import ConfigError


def make_config(**overrides):
    defaults = dict(
        token="fake-token",
        guild=10,
        users=frozenset({1}),
        roles=frozenset(),
        channels=frozenset(),
        gemini_key="fake-gemini",
        composio_key="fake-composio",
        model="gemini-test",
        timezone="UTC",
        mention_channels=frozenset({40}),
        composio_entity="dobby",
        context_limit=50,
        instagram_user_id="",
        notion_parent_page_id="",
    )
    defaults.update(overrides)
    return SimpleNamespace(
        **defaults,
        mentionable=lambda ch: not defaults["mention_channels"] or ch in defaults["mention_channels"],
        allows=lambda g, u, rs, ch: g == 10 and u in defaults["users"],
    )


def make_bot(config=None):
    cfg = config or make_config()
    with (
        patch("bot.integrations.discord.client.get_toolset", return_value=Mock()),
        patch("bot.integrations.discord.client.build_registry", return_value=ToolRegistry(tools=[])),
        patch("bot.integrations.discord.client.Agent"),
    ):
        bot = Bot(cfg)
    bot.agent = AsyncMock()
    bot.agent.run = AsyncMock(return_value=AgentResult("Done."))
    bot.reconcile_calendar_invites = AsyncMock(return_value=0)
    bot._connection = Mock()
    bot._connection.user = Mock(id=5)
    return bot


def fake_session_local():
    mock_ctx = AsyncMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=AsyncMock(commit=AsyncMock()))
    mock_ctx.__aexit__ = AsyncMock(return_value=None)
    return mock_ctx


def make_message(bot, content="<@5> schedule a meeting"):
    msg = Mock(spec=discord.Message)
    msg.author = Mock(bot=False, id=1)
    msg.guild = Mock(id=10)
    msg.channel = Mock(id=40)

    async def empty_history(**kwargs):
        if False:
            yield None

    msg.channel.history = empty_history
    msg.mentions = [bot.user]
    msg.content = content
    msg.reply = AsyncMock()
    msg.reply.return_value = Mock(spec=discord.Message)
    msg.reply.return_value.edit = AsyncMock()
    return msg


# ---------------------------------------------------------------------------
# Command registration
# ---------------------------------------------------------------------------


def test_all_integration_commands_are_registered():
    bot = make_bot()
    names = {c.name for c in bot.tree.get_commands()}
    assert {"email", "help", "schedule", "events", "notion", "instagram", "linkedin"} <= names
    assert "contacts" not in names and "calendar_help" not in names
    groups = {c.name: {s.name for s in c.commands} for c in bot.tree.get_commands() if hasattr(c, "commands")}
    assert groups == {
        "notion": {"search", "note"},
        "instagram": {"posts", "post", "story"},
        "linkedin": {"post"},
    }


# ---------------------------------------------------------------------------
# Authorization / cooldown
# ---------------------------------------------------------------------------


def make_interaction(user_id=1, guild_id=10):
    interaction = Mock()
    interaction.guild_id = guild_id
    interaction.user.id = user_id
    interaction.user.roles = []
    interaction.channel_id = 99
    return interaction


def test_allowed_checks_user_and_guild():
    bot = make_bot()
    assert bot.allowed(make_interaction())
    assert not bot.allowed(make_interaction(user_id=999))
    assert not bot.allowed(make_interaction(guild_id=99))


def test_take_cooldown_is_per_user():
    bot = make_bot()
    assert bot.take_cooldown(1) is True
    assert bot.take_cooldown(1) is False
    assert bot.take_cooldown(2) is True


# ---------------------------------------------------------------------------
# on_message
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("tweak", ["bot_author", "no_mention", "wrong_guild", "not_allowed"])
def test_on_message_ignores_or_rejects(tweak):
    async def run():
        bot = make_bot()
        msg = make_message(bot)
        allowed = True
        if tweak == "bot_author":
            msg.author.bot = True
        elif tweak == "no_mention":
            msg.mentions = []
        elif tweak == "wrong_guild":
            msg.guild.id = 99
        else:
            allowed = False
        with patch.object(bot, "member_allowed", new=AsyncMock(return_value=allowed)):
            await bot.on_message(msg)
        bot.agent.run.assert_not_awaited()

    asyncio.run(run())


def test_on_message_calls_agent_with_live_context_and_mentions():
    async def run():
        bot = make_bot(make_config(context_limit=7))
        msg = make_message(bot, "<@5> schedule a meeting tomorrow with <@1>")
        people = [{"display_name": "Maya", "calendar_email": "maya@uw.edu"}]
        with (
            patch.object(bot, "member_allowed", new=AsyncMock(return_value=True)),
            patch("bot.integrations.discord.client.SessionLocal") as mock_sl,
            patch(
                "bot.integrations.discord.client.gather_context",
                new=AsyncMock(return_value=["[10:00] Maya: hi"]),
            ) as gather,
            patch(
                "bot.integrations.discord.client.resolve_mentions",
                new=AsyncMock(return_value=("schedule a meeting tomorrow with @Maya", people)),
            ),
        ):
            mock_sl.return_value = fake_session_local()
            await bot.on_message(msg)

        gather.assert_awaited_once_with(msg.channel, limit=7, before=msg)
        kwargs = bot.agent.run.await_args.kwargs
        assert kwargs["request"] == "schedule a meeting tomorrow with @Maya"
        assert kwargs["context"] == ["[10:00] Maya: hi"]
        assert kwargs["known_people"] == people
        assert kwargs["discord_user_id"] == "1"
        msg.reply.return_value.edit.assert_awaited_once_with(content="Done.", view=None)

    asyncio.run(run())


def test_on_message_respects_mention_channels():
    async def run():
        bot = make_bot(make_config(mention_channels=frozenset({40})))
        msg = make_message(bot)
        msg.channel.id = 99
        with patch.object(bot, "member_allowed", new=AsyncMock()) as ma:
            await bot.on_message(msg)
        ma.assert_not_awaited()
        bot.agent.run.assert_not_awaited()

    asyncio.run(run())


def test_on_message_reports_failures_in_dobbys_voice():
    async def run():
        bot = make_bot()
        msg = make_message(bot)
        bot.agent.run = AsyncMock(side_effect=RuntimeError("boom"))
        with (
            patch.object(bot, "member_allowed", new=AsyncMock(return_value=True)),
            patch("bot.integrations.discord.client.SessionLocal") as mock_sl,
            patch("bot.integrations.discord.client.gather_context", new=AsyncMock(return_value=[])),
            patch("bot.integrations.discord.client.resolve_mentions", new=AsyncMock(return_value=("x", []))),
        ):
            mock_sl.return_value = fake_session_local()
            await bot.on_message(msg)
        content = msg.reply.return_value.edit.await_args.kwargs["content"]
        assert "could not complete" in content

    asyncio.run(run())


# ---------------------------------------------------------------------------
# send_result / confirm gate
# ---------------------------------------------------------------------------


def test_send_result_attaches_confirm_view_when_something_is_pending():
    async def run():
        bot = make_bot()
        pending = PendingAction("linkedin", "LinkedIn post", "hello world", AsyncMock())
        interaction = Mock(spec=discord.Interaction)
        interaction.guild_id, interaction.channel_id = 10, 40
        interaction.edit_original_response = AsyncMock(return_value="the-message")
        await bot.send_result(interaction, AgentResult("Here is a draft.", [pending]), requester_id=1)
        kwargs = interaction.edit_original_response.await_args.kwargs
        assert "Here is a draft." in kwargs["content"] and "hello world" in kwargs["content"]
        view = kwargs["view"]
        assert view.pending == [pending] and view.requester_id == 1
        assert (view.guild_id, view.channel_id) == ("10", "40")
        assert view.message == "the-message"
        pending.execute.assert_not_awaited()  # nothing runs until Confirm

    asyncio.run(run())


def test_send_result_without_pending_has_no_view():
    async def run():
        bot = make_bot()
        target = Mock(spec=discord.Message)
        target.edit = AsyncMock()
        await bot.send_result(target, AgentResult("x" * 3000), requester_id=1)
        kwargs = target.edit.await_args.kwargs
        assert kwargs["view"] is None and len(kwargs["content"]) == 2000

    asyncio.run(run())


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

BASE_ENV = {
    "DOBBY_ENV_FILE": "/nonexistent",
    "DISCORD_TOKEN": "t",
    "DISCORD_GUILD_ID": "10",
    "GEMINI_API_KEY": "g",
    "COMPOSIO_API_KEY": "c",
    "DATABASE_URL": "x",
    "ALLOWED_USER_IDS": "1",
}


def test_config_permissions_are_fail_closed(monkeypatch):
    for key, value in {
        **BASE_ENV,
        "ALLOWED_USER_IDS": "20",
        "ALLOWED_ROLE_IDS": "30",
        "ALLOWED_CHANNEL_IDS": "40",
    }.items():
        monkeypatch.setenv(key, value)
    cfg = Config.load()
    assert cfg.allows(10, 20, [], 40)
    assert not cfg.allows(10, 99, [], 40)
    assert not cfg.allows(11, 20, [], 40)
    assert not cfg.allows(10, 99, [31], 40)
    assert cfg.allows(10, 99, [30], 40)
    assert not cfg.allows(10, 20, [], 41)


def test_config_reads_context_limit_and_optional_ids(monkeypatch):
    for key, value in BASE_ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("CONTEXT_MESSAGE_LIMIT", raising=False)
    cfg = Config.load()
    assert cfg.context_limit == 50 and cfg.composio_entity == "dobby"
    assert cfg.instagram_user_id == "" and cfg.notion_parent_page_id == ""
    monkeypatch.setenv("CONTEXT_MESSAGE_LIMIT", "20")
    monkeypatch.setenv("INSTAGRAM_USER_ID", " 1784 ")
    monkeypatch.setenv("COMPOSIO_ENTITY_ID", "team-bot")
    cfg = Config.load()
    assert cfg.context_limit == 20 and cfg.instagram_user_id == "1784" and cfg.composio_entity == "team-bot"
    for bad in ("abc", "-1", "9999"):
        monkeypatch.setenv("CONTEXT_MESSAGE_LIMIT", bad)
        with pytest.raises(ConfigError):
            Config.load()
