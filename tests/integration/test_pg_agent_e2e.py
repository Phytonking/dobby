"""End-to-end agent pipeline against real Postgres.

Same code path as a Discord mention (bot/main.py on_message → Agent.run):
history load, tool-call loop, tool execution, jsonb persistence. The Modal
endpoint and Composio are scripted fakes; the database is real.
"""

import json
import uuid
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import patch

from bot.agent import Agent

from .conftest import run_db


# ---------------------------------------------------------------------------
# Scripted fakes
# ---------------------------------------------------------------------------


def text_response(text):
    message = SimpleNamespace(content=text, tool_calls=None)
    return SimpleNamespace(choices=[SimpleNamespace(message=message)])


def tool_call_response(tool_name, args, call_id="call_1"):
    function = SimpleNamespace(name=tool_name, arguments=json.dumps(args))
    tool_call = SimpleNamespace(id=call_id, function=function)
    message = SimpleNamespace(content=None, tool_calls=[tool_call])
    return SimpleNamespace(choices=[SimpleNamespace(message=message)])


class FakeOpenAIClient:
    """Pops scripted responses; records every chat.completions.create call."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []
        completions = SimpleNamespace(create=self._create)
        self.chat = SimpleNamespace(completions=completions)

    def _create(self, *, model, messages, **kwargs):
        self.calls.append({"model": model, "messages": list(messages), "kwargs": kwargs})
        return self._responses.pop(0)


class FakeComposioSession:
    """Records tool executions, returns canned data."""

    def __init__(self, result=None):
        self.executed = []
        self._result = result or {"id": "evt_1", "status": "confirmed"}

    def execute(self, tool_name, arguments):
        self.executed.append((tool_name, arguments))
        return self._result

    def tools(self):
        return []


@contextmanager
def running_agent(responses, composio_session=None):
    """Agent wired to scripted Modal endpoint/Composio. Must be constructed
    inside the patches or __init__ builds a real OpenAI client and run() hits
    the network."""
    config = SimpleNamespace(
        modal_base_url="https://fake.modal.direct/v1",
        modal_token="fake-id.fake-secret",
        composio_key="fake",
        composio_entity_id="test-entity",
        model="Qwen/Qwen3.8-2.4T-A95B",
        timezone="UTC",
        reasoning_effort=None,
        max_tool_calls=8,
        max_completion_tokens=4096,
    )
    fake_client = FakeOpenAIClient(responses)
    composio = composio_session or FakeComposioSession()
    with (
        patch("bot.agent.OpenAI", return_value=fake_client),
        patch("bot.agent.create_composio_session", return_value=(object(), composio)),
    ):
        yield Agent(config), fake_client, composio


def ids():
    return uuid.uuid4().hex, "chan-1"


async def ask(agent, session, guild, channel, text):
    return await agent.run(
        session=session,
        request=text,
        guild_id=guild,
        channel_id=channel,
        discord_user_id="42",
        entity_id="test-entity",
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_message_gets_reply_and_both_turns_persist(migrated_db):
    guild, channel = ids()

    async def check(session):
        with running_agent([text_response("Hi! I'm Dobby.")]) as (agent, _, _):
            reply = await ask(agent, session, guild, channel, "hello there")
        assert reply == "Hi! I'm Dobby."

        from bot.memory import load_history

        rows = await load_history(session, guild, channel)
        assert [(r["role"], r["content"]) for r in rows] == [
            ("user", "hello there"),
            ("model", "Hi! I'm Dobby."),
        ]

    run_db(check)


def test_second_message_sees_first_in_context(migrated_db):
    guild, channel = ids()

    async def check(session):
        script = [text_response("Noted: pizza."), text_response("You like pizza.")]
        with running_agent(script) as (agent, client, _):
            await ask(agent, session, guild, channel, "I like pizza")
            reply = await ask(agent, session, guild, channel, "what do I like?")
        assert reply == "You like pizza."

        # Second call must contain the full first exchange from the DB.
        second_messages = client.calls[1]["messages"]
        texts = [m["content"] for m in second_messages if m.get("content")]
        assert "I like pizza" in texts
        assert "Noted: pizza." in texts
        assert texts[-1] == "what do I like?"

    run_db(check)


def test_tool_call_executes_and_persists_jsonb_turn(migrated_db):
    guild, channel = ids()
    composio = FakeComposioSession(result={"id": "evt_99"})
    script = [
        tool_call_response("GOOGLECALENDAR_CREATE_EVENT", {"summary": "Standup"}, call_id="call_abc"),
        text_response("Created the event."),
    ]

    async def check(session):
        with running_agent(script, composio_session=composio) as (agent, client, _):
            reply = await ask(agent, session, guild, channel, "schedule standup tomorrow")
        assert reply == "Created the event."
        assert composio.executed == [("GOOGLECALENDAR_CREATE_EVENT", {"summary": "Standup"})]

        from bot.memory import load_history

        rows = await load_history(session, guild, channel)
        assert [r["role"] for r in rows] == ["user", "tool", "model"]
        tool_turn = rows[1]
        assert tool_turn["tool_name"] == "GOOGLECALENDAR_CREATE_EVENT"
        assert tool_turn["tool_input"] == {"summary": "Standup"}
        assert tool_turn["tool_result"] == {"success": True, "data": {"id": "evt_99"}}

        # The tool result was fed back with a matching tool_call_id.
        second_messages = client.calls[1]["messages"]
        tool_msgs = [m for m in second_messages if m["role"] == "tool"]
        assert len(tool_msgs) == 1
        assert tool_msgs[0]["tool_call_id"] == "call_abc"
        assert json.loads(tool_msgs[0]["content"]) == {"success": True, "data": {"id": "evt_99"}}

    run_db(check)


def test_conversation_survives_new_agent_instance(migrated_db):
    """Bot restart: fresh Agent, same DB — context must survive."""
    guild, channel = ids()

    async def check(session):
        with running_agent([text_response("Remembered.")]) as (agent1, _, _):
            await ask(agent1, session, guild, channel, "my deadline is Friday")

        with running_agent([text_response("Your deadline is Friday.")]) as (agent2, client2, _):
            reply = await ask(agent2, session, guild, channel, "when is my deadline?")
        assert reply == "Your deadline is Friday."
        texts = [m["content"] for m in client2.calls[0]["messages"] if m.get("content")]
        assert "my deadline is Friday" in texts

    run_db(check)
