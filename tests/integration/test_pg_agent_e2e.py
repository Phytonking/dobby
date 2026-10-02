"""The real agent loop against real Postgres: model -> registry -> tools -> audit rows.

The model is an httpx MockTransport speaking OpenAI Chat Completions and Composio is a fake
toolset; everything between (registry, calendar proposal handler, lookup SQL, record_action) is
production code.
"""

import json
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import sqlalchemy as sa

from bot.AIModels import Endpoint, ModelSettings
from bot.agent import Agent
from bot.integrations import build_registry, google_calendar

from .conftest import run_db


class FakeComposioTools:
    def get_raw_composio_tools(self, tools):
        return [
            SimpleNamespace(slug=t, description=t, input_parameters={"type": "object", "properties": {}})
            for t in tools
        ]


TOOLSET = SimpleNamespace(tools=FakeComposioTools())


def install_model(monkeypatch, replies):
    """Script the model's replies in order; returns the request bodies it received."""
    seen = []

    def handle(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200, json=replies[len(seen) - 1])

    client_class = httpx.AsyncClient
    monkeypatch.setattr(
        "bot.AIModels.httpx.AsyncClient",
        lambda **kwargs: client_class(transport=httpx.MockTransport(handle), **kwargs),
    )
    return seen


def tool_call(name, args):
    call = {"id": "call_1", "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}
    return {"choices": [{"message": {"role": "assistant", "content": None, "tool_calls": [call]}}]}


def reply(text):
    return {"choices": [{"message": {"role": "assistant", "content": text}}]}


def make_agent():
    endpoint = Endpoint("openai-compatible", "test-model", "key", "https://model.test/v1")
    config = SimpleNamespace(ai_settings=ModelSettings(endpoint), timezone="UTC", composio_entity="dobby")
    agent = Agent(config, build_registry(TOOLSET, (google_calendar.INTEGRATION,)))
    agent.toolset = TOOLSET
    return agent


async def audit(session, guild):
    rows = await session.execute(
        sa.text("SELECT tool, status FROM agent_actions WHERE guild_id = :g ORDER BY id"), {"g": guild}
    )
    return [tuple(r) for r in rows]


def test_lookup_tool_reads_real_users_table_and_is_audited(migrated_db, monkeypatch):
    tag, guild, requester = uuid.uuid4().hex[:8], uuid.uuid4().hex, uuid.uuid4().hex
    seen = install_model(
        monkeypatch, [tool_call("lookup_calendar_email", {"name": f"Maya {tag}"}), reply("Dobby found Maya!")]
    )

    async def check(session):
        await session.execute(
            sa.text("INSERT INTO users (discord_id, display_name, calendar_email) VALUES (:d, :n, :e)"),
            {"d": uuid.uuid4().hex, "n": f"Maya {tag}", "e": f"maya{tag}@uw.edu"},
        )
        await session.commit()

        result = await make_agent().run(session, f"invite Maya {tag}", guild, "chan", requester)

        assert result.text == "Dobby found Maya!"
        assert await audit(session, guild) == [("lookup_calendar_email", "ok")]

    run_db(check)
    tool_message = next(m for m in seen[1]["messages"] if m["role"] == "tool")
    assert json.loads(tool_message["content"])["email"] == f"maya{tag}@uw.edu"


def test_calendar_create_is_queued_for_confirmation_not_executed(migrated_db, monkeypatch):
    guild = uuid.uuid4().hex
    event = {"summary": "Leonard party session", "start_datetime": "2026-10-03T17:00:00"}
    install_model(
        monkeypatch, [tool_call("GOOGLECALENDAR_CREATE_EVENT", event), reply("Please confirm in Discord.")]
    )

    async def check(session):
        with (
            patch("bot.agent.run_action", new=AsyncMock()) as composio_direct,
            patch(
                "bot.integrations.google_calendar.proposals.run_action", new=AsyncMock()
            ) as composio_proposal,
        ):
            result = await make_agent().run(session, "party saturday 5pm", guild, "chan", "1")

        assert len(result.pending) == 1
        assert "Leonard party session" in result.pending[0].preview
        composio_direct.assert_not_awaited()
        composio_proposal.assert_not_awaited()
        assert await audit(session, guild) == [("GOOGLECALENDAR_CREATE_EVENT", "ok")]

    run_db(check)


def test_failed_composio_tool_is_audited_as_error_and_reported_to_the_model(migrated_db, monkeypatch):
    guild = uuid.uuid4().hex
    seen = install_model(
        monkeypatch, [tool_call("GOOGLECALENDAR_FIND_EVENT", {"query": "sync"}), reply("Oh dear, it failed.")]
    )
    failure = {"success": False, "error": "no connected account"}

    async def check(session):
        with patch("bot.agent.run_action", new=AsyncMock(return_value=failure)):
            result = await make_agent().run(session, "find the sync", guild, "chan", "1")

        assert result.text == "Oh dear, it failed."
        assert await audit(session, guild) == [("GOOGLECALENDAR_FIND_EVENT", "error")]

    run_db(check)
    tool_message = next(m for m in seen[1]["messages"] if m["role"] == "tool")
    assert json.loads(tool_message["content"]) == failure
