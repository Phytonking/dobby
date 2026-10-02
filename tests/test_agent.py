"""Tests for the OpenAI-wire-format ReAct agent loop (bot/agent.py).

All external calls (the Modal endpoint, Composio, DB) are mocked.
"""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from bot.agent import Agent, _history_to_messages


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_config(**overrides):
    defaults = dict(
        modal_base_url="https://fake.modal.direct/v1",
        modal_token="fake-id.fake-secret",
        composio_key="fake-composio",
        composio_entity_id="default",
        model="Qwen/Qwen3.8-2.4T-A95B",
        timezone="UTC",
        reasoning_effort=None,
        max_tool_calls=8,
        max_completion_tokens=4096,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def make_text_response(text):
    """Fake ChatCompletion with plain text content and no tool calls."""
    message = SimpleNamespace(content=text, tool_calls=None)
    return SimpleNamespace(choices=[SimpleNamespace(message=message)])


def make_tool_call_response(tool_name, args, call_id="call_1"):
    """Fake ChatCompletion with a single tool call (no text)."""
    function = SimpleNamespace(name=tool_name, arguments=json.dumps(args))
    tool_call = SimpleNamespace(id=call_id, function=function)
    message = SimpleNamespace(content=None, tool_calls=[tool_call])
    return SimpleNamespace(choices=[SimpleNamespace(message=message)])


def make_parallel_tool_call_response(tool_name, args, count):
    """Fake ChatCompletion with several tool_calls in a single response —
    the shape a model uses for parallel tool calling."""
    calls = [
        SimpleNamespace(id=f"call_{i}", function=SimpleNamespace(name=tool_name, arguments=json.dumps(args)))
        for i in range(count)
    ]
    message = SimpleNamespace(content=None, tool_calls=calls)
    return SimpleNamespace(choices=[SimpleNamespace(message=message)])


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_agent_returns_text_on_first_call():
    async def run():
        config = make_config()
        with (
            patch("bot.agent.OpenAI") as mock_client,
            patch("bot.agent.create_composio_session", return_value=(Mock(), Mock())),
            patch("bot.agent.get_openai_tools", return_value=[]),
            patch("bot.agent.load_history", new=AsyncMock(return_value=[])),
            patch("bot.agent.append_turn", new=AsyncMock()),
        ):
            mock_client.return_value.chat.completions.create.return_value = make_text_response(
                "Event created."
            )
            agent = Agent(config)
            session = AsyncMock()
            session.commit = AsyncMock()
            result = await agent.run(
                session=session,
                request="Schedule a meeting tomorrow at 10am",
                guild_id="10",
                channel_id="40",
                discord_user_id="1",
                entity_id="1",
            )
        assert result == "Event created."

    asyncio.run(run())


def test_agent_executes_tool_call_then_returns_text():
    async def run():
        config = make_config()
        mock_execute = Mock(return_value={"success": True, "data": {"id": "abc"}})

        with (
            patch("bot.agent.OpenAI") as mock_client,
            patch("bot.agent.create_composio_session", return_value=(Mock(), Mock())),
            patch("bot.agent.get_openai_tools", return_value=[]),
            patch("bot.agent.execute_tool", mock_execute),
            patch("bot.agent.load_history", new=AsyncMock(return_value=[])),
            patch("bot.agent.append_turn", new=AsyncMock()),
        ):
            mock_client.return_value.chat.completions.create.side_effect = [
                make_tool_call_response("GOOGLECALENDAR_CREATE_EVENT", {"summary": "Sync"}),
                make_text_response("Meeting created."),
            ]
            agent = Agent(config)
            session = AsyncMock()
            session.commit = AsyncMock()
            result = await agent.run(
                session=session,
                request="Create a meeting",
                guild_id="10",
                channel_id="40",
                discord_user_id="1",
                entity_id="1",
            )

        assert result == "Meeting created."
        mock_execute.assert_called_once_with(
            agent._session, "GOOGLECALENDAR_CREATE_EVENT", {"summary": "Sync"}
        )

    asyncio.run(run())


def test_agent_feeds_tool_result_back_with_matching_call_id():
    async def run():
        config = make_config()

        with (
            patch("bot.agent.OpenAI") as mock_client,
            patch("bot.agent.create_composio_session", return_value=(Mock(), Mock())),
            patch("bot.agent.get_openai_tools", return_value=[]),
            patch("bot.agent.execute_tool", return_value={"success": True, "data": {"id": "abc"}}),
            patch("bot.agent.load_history", new=AsyncMock(return_value=[])),
            patch("bot.agent.append_turn", new=AsyncMock()),
        ):
            mock_create = mock_client.return_value.chat.completions.create
            mock_create.side_effect = [
                make_tool_call_response(
                    "GOOGLECALENDAR_CREATE_EVENT", {"summary": "Sync"}, call_id="call_xyz"
                ),
                make_text_response("Meeting created."),
            ]
            agent = Agent(config)
            session = AsyncMock()
            session.commit = AsyncMock()
            await agent.run(
                session=session,
                request="Create a meeting",
                guild_id="10",
                channel_id="40",
                discord_user_id="1",
                entity_id="1",
            )

        second_messages = mock_create.call_args_list[1].kwargs["messages"]
        assistant_msg = next(m for m in second_messages if m["role"] == "assistant" and m.get("tool_calls"))
        tool_msg = next(m for m in second_messages if m["role"] == "tool")
        assert assistant_msg["tool_calls"][0]["id"] == "call_xyz"
        assert tool_msg["tool_call_id"] == "call_xyz"
        assert json.loads(tool_msg["content"]) == {"success": True, "data": {"id": "abc"}}

    asyncio.run(run())


def test_agent_stops_after_max_tool_calls():
    async def run():
        config = make_config(max_tool_calls=3)

        with (
            patch("bot.agent.OpenAI") as mock_client,
            patch("bot.agent.create_composio_session", return_value=(Mock(), Mock())),
            patch("bot.agent.get_openai_tools", return_value=[]),
            patch("bot.agent.execute_tool", return_value={"success": True, "data": {}}),
            patch("bot.agent.load_history", new=AsyncMock(return_value=[])),
            patch("bot.agent.append_turn", new=AsyncMock()),
        ):
            # Always return a tool call → hits config.max_tool_calls
            mock_client.return_value.chat.completions.create.return_value = make_tool_call_response(
                "LOOP", {}
            )
            agent = Agent(config)
            session = AsyncMock()
            session.commit = AsyncMock()
            result = await agent.run(
                session=session,
                request="do something",
                guild_id="10",
                channel_id="40",
                discord_user_id="1",
                entity_id="1",
            )

        assert "tool call limit" in result

    asyncio.run(run())


def test_agent_caps_tool_calls_within_a_single_batched_response():
    """A model can return many tool_calls in one response (parallel tool
    calling) — the cap must hold mid-batch, not just between round trips,
    or one response can execute far more actions than max_tool_calls allows."""

    async def run():
        config = make_config(max_tool_calls=8)
        mock_execute = Mock(return_value={"success": True, "data": {}})

        with (
            patch("bot.agent.OpenAI") as mock_client,
            patch("bot.agent.create_composio_session", return_value=(Mock(), Mock())),
            patch("bot.agent.get_openai_tools", return_value=[]),
            patch("bot.agent.execute_tool", mock_execute),
            patch("bot.agent.load_history", new=AsyncMock(return_value=[])),
            patch("bot.agent.append_turn", new=AsyncMock()),
        ):
            # One response, 12 parallel tool_calls — well past max_tool_calls (8).
            mock_client.return_value.chat.completions.create.return_value = make_parallel_tool_call_response(
                "GOOGLECALENDAR_CREATE_EVENT", {}, count=12
            )
            agent = Agent(config)
            session = AsyncMock()
            session.commit = AsyncMock()
            result = await agent.run(
                session=session,
                request="create 12 events",
                guild_id="10",
                channel_id="40",
                discord_user_id="1",
                entity_id="1",
            )

        assert "tool call limit" in result
        assert mock_execute.call_count == 8

    asyncio.run(run())


def test_agent_logs_truncated_response_but_still_returns_it():
    async def run():
        config = make_config()

        with (
            patch("bot.agent.OpenAI") as mock_client,
            patch("bot.agent.create_composio_session", return_value=(Mock(), Mock())),
            patch("bot.agent.get_openai_tools", return_value=[]),
            patch("bot.agent.load_history", new=AsyncMock(return_value=[])),
            patch("bot.agent.append_turn", new=AsyncMock()),
        ):
            response = make_text_response("cut off mid-")
            response.choices[0].finish_reason = "length"
            mock_client.return_value.chat.completions.create.return_value = response
            agent = Agent(config)
            session = AsyncMock()
            session.commit = AsyncMock()
            result = await agent.run(
                session=session,
                request="test",
                guild_id="10",
                channel_id="40",
                discord_user_id="1",
                entity_id="1",
            )

        assert result == "cut off mid-"

    asyncio.run(run())


def test_agent_handles_unparseable_tool_arguments():
    async def run():
        config = make_config()
        mock_execute = Mock(return_value={"success": True, "data": {}})

        with (
            patch("bot.agent.OpenAI") as mock_client,
            patch("bot.agent.create_composio_session", return_value=(Mock(), Mock())),
            patch("bot.agent.get_openai_tools", return_value=[]),
            patch("bot.agent.execute_tool", mock_execute),
            patch("bot.agent.load_history", new=AsyncMock(return_value=[])),
            patch("bot.agent.append_turn", new=AsyncMock()),
        ):
            bad_call = make_tool_call_response("GOOGLECALENDAR_CREATE_EVENT", {})
            bad_call.choices[0].message.tool_calls[0].function.arguments = "{not valid json"
            mock_client.return_value.chat.completions.create.side_effect = [
                bad_call,
                make_text_response("handled"),
            ]
            agent = Agent(config)
            session = AsyncMock()
            session.commit = AsyncMock()
            result = await agent.run(
                session=session,
                request="do something",
                guild_id="10",
                channel_id="40",
                discord_user_id="1",
                entity_id="1",
            )

        assert result == "handled"
        mock_execute.assert_called_once_with(agent._session, "GOOGLECALENDAR_CREATE_EVENT", {})

    asyncio.run(run())


def test_agent_returns_error_message_on_endpoint_failure():
    async def run():
        config = make_config()

        class FakeAPIError(Exception):
            status_code = 503

        with (
            patch("bot.agent.OpenAI") as mock_client,
            patch("bot.agent.create_composio_session", return_value=(Mock(), Mock())),
            patch("bot.agent.get_openai_tools", return_value=[]),
            patch("bot.agent.load_history", new=AsyncMock(return_value=[])),
            patch("bot.agent.append_turn", new=AsyncMock()),
            patch("bot.agent.APIError", FakeAPIError),
        ):
            mock_client.return_value.chat.completions.create.side_effect = FakeAPIError()
            agent = Agent(config)
            session = AsyncMock()
            session.commit = AsyncMock()
            result = await agent.run(
                session=session,
                request="do something",
                guild_id="10",
                channel_id="40",
                discord_user_id="1",
                entity_id="1",
            )

        assert "unavailable" in result.lower()

    asyncio.run(run())


def test_agent_reports_friendly_error_when_composio_init_fails():
    async def run():
        config = make_config()

        with (
            patch("bot.agent.OpenAI"),
            patch("bot.agent.create_composio_session", side_effect=RuntimeError("bad key")),
            patch("bot.agent.load_history", new=AsyncMock(return_value=[])),
            patch("bot.agent.append_turn", new=AsyncMock()),
        ):
            agent = Agent(config)
            session = AsyncMock()
            session.commit = AsyncMock()
            result = await agent.run(
                session=session,
                request="do something",
                guild_id="10",
                channel_id="40",
                discord_user_id="1",
                entity_id="1",
            )

        assert "not configured" in result.lower()

    asyncio.run(run())


def test_agent_omits_tools_key_when_no_tools_available():
    async def run():
        config = make_config()

        with (
            patch("bot.agent.OpenAI") as mock_client,
            patch("bot.agent.create_composio_session", return_value=(Mock(), Mock())),
            patch("bot.agent.get_openai_tools", return_value=[]),
            patch("bot.agent.load_history", new=AsyncMock(return_value=[])),
            patch("bot.agent.append_turn", new=AsyncMock()),
        ):
            mock_create = mock_client.return_value.chat.completions.create
            mock_create.return_value = make_text_response("ok")
            agent = Agent(config)
            session = AsyncMock()
            session.commit = AsyncMock()
            await agent.run(
                session=session,
                request="test",
                guild_id="10",
                channel_id="40",
                discord_user_id="1",
                entity_id="1",
            )

        # tools=None would serialize as JSON null on the wire; some
        # OpenAI-compatible servers reject that for an array-typed field.
        assert "tools" not in mock_create.call_args.kwargs

    asyncio.run(run())


def test_agent_passes_reasoning_effort_when_configured():
    async def run():
        config = make_config(reasoning_effort="xhigh")

        with (
            patch("bot.agent.OpenAI") as mock_client,
            patch("bot.agent.create_composio_session", return_value=(Mock(), Mock())),
            patch("bot.agent.get_openai_tools", return_value=[]),
            patch("bot.agent.load_history", new=AsyncMock(return_value=[])),
            patch("bot.agent.append_turn", new=AsyncMock()),
        ):
            mock_create = mock_client.return_value.chat.completions.create
            mock_create.return_value = make_text_response("ok")
            agent = Agent(config)
            session = AsyncMock()
            session.commit = AsyncMock()
            await agent.run(
                session=session,
                request="test",
                guild_id="10",
                channel_id="40",
                discord_user_id="1",
                entity_id="1",
            )

        assert mock_create.call_args.kwargs["reasoning_effort"] == "xhigh"

    asyncio.run(run())


def test_agent_omits_reasoning_effort_when_not_configured():
    async def run():
        config = make_config(reasoning_effort=None)

        with (
            patch("bot.agent.OpenAI") as mock_client,
            patch("bot.agent.create_composio_session", return_value=(Mock(), Mock())),
            patch("bot.agent.get_openai_tools", return_value=[]),
            patch("bot.agent.load_history", new=AsyncMock(return_value=[])),
            patch("bot.agent.append_turn", new=AsyncMock()),
        ):
            mock_create = mock_client.return_value.chat.completions.create
            mock_create.return_value = make_text_response("ok")
            agent = Agent(config)
            session = AsyncMock()
            session.commit = AsyncMock()
            await agent.run(
                session=session,
                request="test",
                guild_id="10",
                channel_id="40",
                discord_user_id="1",
                entity_id="1",
            )

        assert "reasoning_effort" not in mock_create.call_args.kwargs

    asyncio.run(run())


def test_history_to_messages_reconstructs_tool_pairs_with_matching_ids():
    rows = [
        {"role": "user", "content": "hello", "tool_name": None, "tool_input": None, "tool_result": None},
        {"role": "model", "content": "hi", "tool_name": None, "tool_input": None, "tool_result": None},
        {
            "role": "tool",
            "content": None,
            "tool_name": "SOME_TOOL",
            "tool_input": {"arg": "val"},
            "tool_result": {"success": True},
        },
    ]
    messages = _history_to_messages(rows)
    # user, assistant text, assistant tool_call carrier, tool response
    assert len(messages) == 4
    assert messages[0] == {"role": "user", "content": "hello"}
    assert messages[1] == {"role": "assistant", "content": "hi"}
    assert messages[2]["role"] == "assistant"
    assert messages[3]["role"] == "tool"
    call_id = messages[2]["tool_calls"][0]["id"]
    assert messages[3]["tool_call_id"] == call_id
    assert messages[2]["tool_calls"][0]["function"]["name"] == "SOME_TOOL"
    assert json.loads(messages[2]["tool_calls"][0]["function"]["arguments"]) == {"arg": "val"}
    assert json.loads(messages[3]["content"]) == {"success": True}


def test_agent_uses_override_timezone():
    async def run():
        config = make_config()

        with (
            patch("bot.agent.OpenAI") as mock_client,
            patch("bot.agent.create_composio_session", return_value=(Mock(), Mock())),
            patch("bot.agent.get_openai_tools", return_value=[]),
            patch("bot.agent.load_history", new=AsyncMock(return_value=[])),
            patch("bot.agent.append_turn", new=AsyncMock()),
        ):
            mock_create = mock_client.return_value.chat.completions.create
            mock_create.return_value = make_text_response("ok")
            agent = Agent(config)
            session = AsyncMock()
            session.commit = AsyncMock()
            await agent.run(
                session=session,
                request="test",
                guild_id="10",
                channel_id="40",
                discord_user_id="1",
                entity_id="1",
                timezone="America/Los_Angeles",
            )
            messages = mock_create.call_args.kwargs["messages"]
            assert "America/Los_Angeles" in messages[0]["content"]

    asyncio.run(run())
