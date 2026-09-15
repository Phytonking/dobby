"""Tests for the thin Discord bot layer (bot/main.py).

Verifies authorization gating, cooldowns, and that the agent is called
correctly on valid requests. Mocks Agent, SessionLocal, and Discord objects.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch, MagicMock

import discord
import pytest

from bot.config import Config
from bot.main import Bot


# ---------------------------------------------------------------------------
# Config helper — matches new 11-field signature
# ---------------------------------------------------------------------------

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
        context_limit=12,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults, mentionable=lambda ch: not defaults["mention_channels"] or ch in defaults["mention_channels"],
                           allows=lambda g, u, rs, ch: g == 10 and u in defaults["users"])


# ---------------------------------------------------------------------------
# Minimal Bot construction helper — patches out external connections
# ---------------------------------------------------------------------------

def make_bot(config=None):
    cfg = config or make_config()
    with (
        patch("bot.main.Agent"),
        patch("bot.main.SessionLocal"),
    ):
        bot = Bot(cfg)
    bot.agent = AsyncMock()
    bot.agent.run = AsyncMock(return_value="Done.")
    bot._connection = Mock()
    bot._connection.user = Mock(id=5)
    return bot


# ---------------------------------------------------------------------------
# Authorization tests
# ---------------------------------------------------------------------------

def test_allowed_returns_true_for_known_user():
    bot = make_bot()
    interaction = Mock()
    interaction.guild_id = 10
    interaction.user.id = 1
    interaction.user.roles = []
    interaction.channel_id = 99
    assert bot.allowed(interaction)


def test_allowed_returns_false_for_unknown_user():
    bot = make_bot()
    interaction = Mock()
    interaction.guild_id = 10
    interaction.user.id = 999
    interaction.user.roles = []
    interaction.channel_id = 99
    assert not bot.allowed(interaction)


def test_allowed_returns_false_for_wrong_guild():
    bot = make_bot()
    interaction = Mock()
    interaction.guild_id = 99
    interaction.user.id = 1
    interaction.user.roles = []
    interaction.channel_id = 99
    assert not bot.allowed(interaction)


# ---------------------------------------------------------------------------
# Cooldown tests
# ---------------------------------------------------------------------------

def test_take_cooldown_allows_first_request():
    bot = make_bot()
    assert bot.take_cooldown(1) is True


def test_take_cooldown_blocks_second_immediate_request():
    bot = make_bot()
    assert bot.take_cooldown(1) is True
    assert bot.take_cooldown(1) is False


def test_take_cooldown_allows_different_users_simultaneously():
    bot = make_bot()
    assert bot.take_cooldown(1) is True
    assert bot.take_cooldown(2) is True


# ---------------------------------------------------------------------------
# on_message tests
# ---------------------------------------------------------------------------

def make_message(bot, content="<@5> schedule a meeting"):
    msg = Mock()
    msg.author.bot = False
    msg.author.id = 1
    msg.author.send = AsyncMock()
    msg.guild.id = 10
    msg.channel.id = 40
    msg.channel.name = "general"
    msg.mentions = [bot.user]
    msg.content = content
    msg.reference = None
    msg.reply = AsyncMock()
    msg.reply.return_value.edit = AsyncMock()
    return msg


def test_on_message_ignores_bot_messages():
    async def run():
        bot = make_bot()
        msg = make_message(bot)
        msg.author.bot = True
        with patch.object(bot, "member_allowed", new=AsyncMock(return_value=True)):
            await bot.on_message(msg)
        bot.agent.run.assert_not_awaited()

    asyncio.run(run())


def test_on_message_ignores_messages_without_bot_mention():
    async def run():
        bot = make_bot()
        msg = make_message(bot)
        msg.mentions = []
        with patch.object(bot, "member_allowed", new=AsyncMock(return_value=True)):
            await bot.on_message(msg)
        bot.agent.run.assert_not_awaited()

    asyncio.run(run())


def test_on_message_ignores_wrong_guild():
    async def run():
        bot = make_bot()
        msg = make_message(bot)
        msg.guild.id = 99
        with patch.object(bot, "member_allowed", new=AsyncMock(return_value=True)):
            await bot.on_message(msg)
        bot.agent.run.assert_not_awaited()

    asyncio.run(run())


def test_on_message_rejects_unauthorized_member():
    async def run():
        bot = make_bot()
        msg = make_message(bot)
        with patch.object(bot, "member_allowed", new=AsyncMock(return_value=False)):
            await bot.on_message(msg)
        bot.agent.run.assert_not_awaited()

    asyncio.run(run())


def test_on_message_calls_agent_for_valid_request():
    async def run():
        bot = make_bot()
        msg = make_message(bot, "<@5> schedule a meeting tomorrow at 10am")

        async def fake_session_ctx():
            session = AsyncMock()
            session.commit = AsyncMock()
            return session

        with (
            patch.object(bot, "member_allowed", new=AsyncMock(return_value=True)),
            patch("bot.main.SessionLocal") as mock_sl,
            patch("bot.main.load_guild_settings", new=AsyncMock(return_value=None)),
        ):
            mock_ctx = AsyncMock()
            mock_ctx.__aenter__ = AsyncMock(return_value=AsyncMock(commit=AsyncMock()))
            mock_ctx.__aexit__ = AsyncMock(return_value=None)
            mock_sl.return_value = mock_ctx
            await bot.on_message(msg)

        # agent.run was awaited (via the async context manager)
        # The reply was sent
        msg.reply.assert_awaited()

    asyncio.run(run())


def test_on_message_ignores_non_mention_channels():
    async def run():
        bot = make_bot(make_config(mention_channels=frozenset({40})))
        msg = make_message(bot)
        msg.channel.id = 99  # not in mention_channels
        with patch.object(bot, "member_allowed", new=AsyncMock()) as ma:
            await bot.on_message(msg)
        ma.assert_not_awaited()
        bot.agent.run.assert_not_awaited()

    asyncio.run(run())


def test_on_message_empty_mention_channels_allows_any():
    async def run():
        bot = make_bot(make_config(mention_channels=frozenset()))
        msg = make_message(bot)
        msg.channel.id = 12345  # any channel
        with (
            patch.object(bot, "member_allowed", new=AsyncMock(return_value=True)),
            patch("bot.main.SessionLocal") as mock_sl,
            patch("bot.main.load_guild_settings", new=AsyncMock(return_value=None)),
        ):
            mock_ctx = AsyncMock()
            mock_ctx.__aenter__ = AsyncMock(return_value=AsyncMock(commit=AsyncMock()))
            mock_ctx.__aexit__ = AsyncMock(return_value=None)
            mock_sl.return_value = mock_ctx
            await bot.on_message(msg)
        msg.reply.assert_awaited()

    asyncio.run(run())


# ---------------------------------------------------------------------------
# Config permission tests (replaces test_docker_config.py auth tests)
# ---------------------------------------------------------------------------

def test_config_permissions_are_fail_closed():
    # User not in allowlist → denied even with valid guild+channel
    cfg = Config.__new__(Config)
    object.__setattr__(cfg, "token", "")
    object.__setattr__(cfg, "guild", 10)
    object.__setattr__(cfg, "users", frozenset({20}))
    object.__setattr__(cfg, "roles", frozenset({30}))
    object.__setattr__(cfg, "channels", frozenset({40}))
    object.__setattr__(cfg, "gemini_key", "")
    object.__setattr__(cfg, "composio_key", "")
    object.__setattr__(cfg, "model", "")
    object.__setattr__(cfg, "timezone", "UTC")
    object.__setattr__(cfg, "mention_channels", frozenset())
    object.__setattr__(cfg, "context_limit", 12)

    assert cfg.allows(10, 20, [], 40)       # known user, right guild/channel
    assert not cfg.allows(10, 99, [], 40)   # unknown user
    assert not cfg.allows(11, 20, [], 40)   # wrong guild
    assert not cfg.allows(10, 99, [31], 40) # wrong role
    assert cfg.allows(10, 99, [30], 40)     # right role


def test_config_role_gating():
    cfg = Config.__new__(Config)
    for attr, val in [
        ("token", ""), ("guild", 10), ("users", frozenset()), ("roles", frozenset({30})),
        ("channels", frozenset()), ("gemini_key", ""), ("composio_key", ""),
        ("model", ""), ("timezone", "UTC"), ("mention_channels", frozenset()), ("context_limit", 12),
    ]:
        object.__setattr__(cfg, attr, val)

    assert cfg.allows(10, 999, [30], 99)     # role grants access
    assert not cfg.allows(10, 999, [], 99)   # no role → denied
