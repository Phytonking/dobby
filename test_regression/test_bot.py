"""Behavioral bot tests; no assertions about model names, prompts, or model output."""

import asyncio
import os
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import discord
import pytest

from bot.composio import run_action
from bot.config import Config
from bot.integrations import INTEGRATIONS, build_registry
from bot.integrations.base import PendingAction, RunContext
from bot.integrations.discord.client import Bot
from bot.integrations.discord.confirm import ConfirmView
from bot.integrations.discord.context import gather_context
from bot.integrations.instagram import publish as instagram
from bot.integrations.linkedin import publish as linkedin


def config(**overrides):
    values = dict(
        token="test-token",
        guild=10,
        users=frozenset({1, 2}),
        roles=frozenset({5}),
        channels=frozenset({40}),
        gemini_key="test-key",
        composio_key="test-key",
        model="unused",
        timezone="UTC",
        instagram_user_id="",
    )
    return Config(**(values | overrides))


class Provider:
    """External provider contract fake, shared by registry and execution paths."""

    def __init__(self, disconnected=()):
        self.disconnected = disconnected
        self.calls = []
        self.tools = self

    def get_raw_composio_tools(self, *, tools):
        return [SimpleNamespace(slug=name, description=name, input_parameters={}) for name in tools]

    def execute(self, *, slug, arguments, user_id):
        self.calls.append((slug, arguments, user_id))
        if any(slug.lower().startswith(prefix) for prefix in self.disconnected):
            return {"successful": False, "error": "Service account is disconnected"}
        return {"successful": True, "data": {"id": "provider-id", "items": []}}


def make_bot(cfg=None, provider=None):
    with patch("bot.integrations.discord.client.get_toolset", return_value=provider or Provider()):
        return Bot(cfg or config())


def interaction(user=1, guild=10, channel=40, roles=()):
    return SimpleNamespace(
        user=SimpleNamespace(id=user, roles=[SimpleNamespace(id=r) for r in roles]),
        guild_id=guild,
        channel_id=channel,
        response=SimpleNamespace(send_message=AsyncMock(), edit_message=AsyncMock(), defer=AsyncMock()),
        edit_original_response=AsyncMock(),
    )


@pytest.mark.asyncio
async def test_03_bot_calendar_notion_without_social_accounts():
    provider = Provider(disconnected=("instagram", "linkedin"))
    bot = make_bot(provider=provider)
    registry = build_registry(provider)
    assert {"google_calendar", "notion"} <= set(registry.owner.values())
    for integration in INTEGRATIONS[:2]:
        assert integration.actions
        result = await run_action(provider, integration.actions[0], {}, bot.config.composio_entity)
        assert result["success"]
    ctx = RunContext(None, "10", "40", "1", toolset=provider, config=bot.config)
    missing = await instagram.list_tool(ctx, {})
    assert not missing["success"] and "INSTAGRAM_USER_ID" in missing["error"]
    assert not (await linkedin.draft_post(provider, "dobby", "hello").execute())["success"]
    await bot.tree.get_command("help").callback(interaction())
    await bot.close()


@pytest.mark.asyncio
async def test_04_bot_help_and_email_without_any_connections(db, monkeypatch):
    monkeypatch.setattr("bot.integrations.discord.commands.SessionLocal", db[0])
    provider = Provider(disconnected=("googlecalendar", "notion", "instagram", "linkedin"))
    bot = make_bot(provider=provider)
    request = interaction()
    await bot.tree.get_command("help").callback(request)
    request.edit_original_response.assert_awaited_once()
    request = interaction()
    await bot.tree.get_command("email").callback(request, SimpleNamespace(value="set"), "bot@example.org")
    async with db[0]() as session:
        from bot.memory import find_user_by_discord_id

        assert (await find_user_by_discord_id(session, "1"))["calendar_email"] == "bot@example.org"
    assert provider.calls == []
    await bot.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("failed", ["googlecalendar", "notion", "instagram", "linkedin"])
async def test_05_bot_provider_failure_isolation(failed):
    provider = Provider(disconnected=(failed,))
    for prefix in ("googlecalendar", "notion", "instagram", "linkedin"):
        result = await run_action(provider, prefix.upper() + "_READ", {}, "dobby")
        assert result["success"] is (prefix != failed)
    assert len(provider.calls) == 4


@pytest.mark.parametrize(
    "setting,value,expected",
    [
        ("DISCORD_TOKEN", "", "DISCORD_TOKEN"),
        ("DISCORD_GUILD_ID", "bad", "DISCORD_GUILD_ID"),
        ("ALLOWED_USER_IDS", "not-numeric", "numeric"),
        ("TEAM_TIMEZONE", "Invalid/Zone", "TEAM_TIMEZONE"),
        ("CONTEXT_MESSAGE_LIMIT", "501", "CONTEXT_MESSAGE_LIMIT"),
        ("INSTAGRAM_USER_ID", "", None),
    ],
)
def test_06_bot_configuration_cli(setting, value, expected):
    env = os.environ | dict(
        DOBBY_ENV_FILE="/nonexistent.env",
        DISCORD_TOKEN="secret-sentinel",
        DISCORD_GUILD_ID="10",
        GEMINI_API_KEY="secret-sentinel",
        COMPOSIO_API_KEY="secret-sentinel",
        ALLOWED_USER_IDS="1",
        ALLOWED_ROLE_IDS="",
        TEAM_TIMEZONE="UTC",
        CONTEXT_MESSAGE_LIMIT="50",
    )
    env[setting] = value
    result = subprocess.run(
        [sys.executable, "-m", "bot.main", "--check"], env=env, capture_output=True, text=True, timeout=20
    )
    assert result.returncode == (2 if expected else 0), result.stderr
    assert (expected or "config_ok") in result.stderr
    assert "secret-sentinel" not in result.stderr


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "user,guild,channel,roles,allowed",
    [
        (1, 10, 40, (), True),
        (99, 10, 40, (5,), True),
        (99, 10, 40, (), False),
        (1, 999, 40, (), False),
        (1, 10, 41, (), False),
        (1, None, 40, (), False),
    ],
)
async def test_14_bot_discord_gate(user, guild, channel, roles, allowed):
    provider = Provider()
    bot = make_bot(provider=provider)
    request = interaction(user, guild, channel, roles)
    await bot.tree.get_command("help").callback(request)
    assert request.response.defer.await_count == int(allowed)
    assert request.edit_original_response.await_count == int(allowed)
    assert provider.calls == []
    await bot.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("end", ["cancel", "timeout", "stranger"])
async def test_16_bot_confirmation_gate(end):
    execute = AsyncMock(return_value={"success": True})
    view = ConfirmView([PendingAction("linkedin", "Post", "Exact preview", execute)], 1, "10", "40")
    execute.assert_not_awaited()
    if end == "cancel":
        await view.cancel.callback(interaction())
    elif end == "timeout":
        await view.on_timeout()
    else:
        await view.confirm.callback(interaction(user=2))
        execute.assert_not_awaited()
        return
    # A queued click may still be delivered after the buttons were disabled.
    await view.confirm.callback(interaction())
    execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_16_bot_confirm_publishes_previewed_text(db, monkeypatch):
    monkeypatch.setattr("bot.integrations.discord.confirm.SessionLocal", db[0])
    provider = Provider()
    action = linkedin.draft_post(provider, "dobby", "Exact approved text")
    assert not provider.calls and action.preview == "Exact approved text"
    view = ConfirmView([action], 1, "10", "40")
    await view.confirm.callback(interaction())
    published = [call for call in provider.calls if call[0] == linkedin.CREATE_POST]
    assert len(published) == 1 and published[0][1]["commentary"] == action.preview


@pytest.mark.asyncio
@pytest.mark.parametrize("timeout", [False, True])
async def test_17_bot_duplicate_confirmation(db, monkeypatch, timeout):
    monkeypatch.setattr("bot.integrations.discord.confirm.SessionLocal", db[0])

    async def publish():
        await asyncio.sleep(0.01)
        if timeout:
            raise TimeoutError("ambiguous provider outcome")
        return {"success": True}

    execute = AsyncMock(side_effect=publish)
    view = ConfirmView([PendingAction("linkedin", "Post", "Exact preview", execute)], 1, "10", "40")
    await asyncio.gather(view.confirm.callback(interaction()), view.confirm.callback(interaction()))
    await view.confirm.callback(interaction())
    execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_18_bot_drafts_and_cooldowns_are_per_user(db, monkeypatch):
    monkeypatch.setattr("bot.integrations.discord.confirm.SessionLocal", db[0])
    first, second = AsyncMock(return_value={"success": True}), AsyncMock(return_value={"success": True})
    views = [
        ConfirmView([PendingAction("linkedin", "Post", "First", first)], 1, "10", "40"),
        ConfirmView([PendingAction("linkedin", "Post", "Second", second)], 2, "10", "41"),
    ]
    await views[0].confirm.callback(interaction(user=2))
    first.assert_not_awaited()
    await views[1].confirm.callback(interaction(user=2))
    second.assert_awaited_once()
    bot = make_bot()
    assert bot.take_cooldown(1) and bot.take_cooldown(2)
    assert not bot.take_cooldown(1)
    await bot.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    [
        "http://example.org/photo.jpg",
        "httpsomething",
        "file:///etc/passwd",
        "https://127.0.0.1/image",
        "https://user:pass@example.org/image",
    ],
)
async def test_19_bot_invalid_media_url(url):
    provider = Provider()
    ctx = RunContext(None, "10", "40", "1", toolset=provider, config=config(instagram_user_id="account"))
    result = await instagram.draft_post_tool(ctx, {"image_url": url, "caption": "test"})
    assert not result["success"]
    assert not ctx.pending and not provider.calls


@pytest.mark.asyncio
async def test_20_bot_publish_errors_do_not_leak_content(db, monkeypatch, caplog):
    monkeypatch.setattr("bot.integrations.discord.confirm.SessionLocal", db[0])
    marker = "private-content-and-secret-sentinel"
    execute = AsyncMock(return_value={"success": False, "error": marker})
    view = ConfirmView([PendingAction("linkedin", "Post", marker, execute)], 1, "10", "40")
    request = interaction()
    await view.confirm.callback(request)
    assert marker not in caplog.text
    assert marker not in request.edit_original_response.await_args.kwargs["content"]


@pytest.mark.asyncio
async def test_21_bot_command_registration_and_sync():
    bot = make_bot()
    with patch.object(bot.tree, "sync", new=AsyncMock()) as sync:
        await bot.setup_hook()
        assert sync.await_args.kwargs["guild"].id == 10
    commands = bot.tree.get_commands(guild=discord.Object(id=10))
    assert {c.name for c in commands} == {
        "email",
        "help",
        "schedule",
        "events",
        "notion",
        "instagram",
        "linkedin",
    }
    assert len(commands) == len({c.name for c in commands})
    await bot.close()


@pytest.mark.asyncio
async def test_22_bot_reconnect_does_not_duplicate_commands_or_publish():
    provider = Provider()
    bot = make_bot(provider=provider)
    with patch.object(bot.tree, "sync", new=AsyncMock()):
        await bot.setup_hook()
        before = [c.name for c in bot.tree.get_commands()]
        await bot.on_ready()
        await bot.on_ready()
        assert [c.name for c in bot.tree.get_commands()] == before
    assert provider.calls == []
    await bot.close()


@pytest.mark.asyncio
async def test_23_bot_missing_history_permission():
    response = Mock(status=403, reason="Forbidden")
    channel = Mock()
    channel.history.side_effect = discord.Forbidden(response, "history unavailable")
    assert await gather_context(channel, limit=50) == []
    bot = make_bot()
    request = interaction()
    await bot.tree.get_command("help").callback(request)
    request.edit_original_response.assert_awaited_once()
    await bot.close()
