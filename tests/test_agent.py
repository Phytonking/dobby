"""Tests for the Gemini ReAct agent loop (bot/agent.py).

All external calls (Gemini, Composio, DB) are mocked.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from bot.agent import Agent, _history_to_contents


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_config():
    return SimpleNamespace(
        gemini_key="fake-gemini",
        composio_key="fake-composio",
        model="gemini-test",
        timezone="UTC",
    )


def make_text_candidate(text):
    """Fake Gemini candidate with a single text part and no function calls."""
    part = SimpleNamespace(text=text, function_call=None)
    content = SimpleNamespace(parts=[part])
    candidate = SimpleNamespace(content=content)
    response = SimpleNamespace(candidates=[candidate])
    return response


def make_fn_candidate(tool_name, args):
    """Fake Gemini candidate with a function_call part (no text)."""
    # Note: cannot use Mock(name=...) — 'name' is a special Mock attribute.
    # Use SimpleNamespace so .name works as a plain attribute.
    fn_call = SimpleNamespace(name=tool_name, args=args)
    part = SimpleNamespace(text="", function_call=fn_call)
    content = SimpleNamespace(parts=[part])
    candidate = SimpleNamespace(content=content)
    return SimpleNamespace(candidates=[candidate])


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_agent_returns_text_on_first_call():
    async def run():
        config = make_config()
        with (
            patch("bot.agent.genai.Client") as mock_client,
            patch("bot.agent.get_toolset", return_value=Mock()),
            patch("bot.agent.get_gemini_tools", return_value=[]),
            patch("bot.agent.load_history", new=AsyncMock(return_value=[])),
            patch("bot.agent.append_turn", new=AsyncMock()),
        ):
            mock_client.return_value.models.generate_content.return_value = make_text_candidate("Event created.")
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
            patch("bot.agent.genai.Client") as mock_client,
            patch("bot.agent.get_toolset", return_value=Mock()),
            patch("bot.agent.get_gemini_tools", return_value=[]),
            patch("bot.agent.execute_tool", mock_execute),
            patch("bot.agent.load_history", new=AsyncMock(return_value=[])),
            patch("bot.agent.append_turn", new=AsyncMock()),
        ):
            mock_client.return_value.models.generate_content.side_effect = [
                make_fn_candidate("GOOGLECALENDAR_CREATE_EVENT", {"summary": "Sync"}),
                make_text_candidate("Meeting created."),
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
            agent.toolset, "GOOGLECALENDAR_CREATE_EVENT", {"summary": "Sync"}, "1"
        )

    asyncio.run(run())


def test_agent_stops_after_max_tool_calls():
    async def run():
        config = make_config()

        with (
            patch("bot.agent.genai.Client") as mock_client,
            patch("bot.agent.get_toolset", return_value=Mock()),
            patch("bot.agent.get_gemini_tools", return_value=[]),
            patch("bot.agent.execute_tool", return_value={"success": True, "data": {}}),
            patch("bot.agent.load_history", new=AsyncMock(return_value=[])),
            patch("bot.agent.append_turn", new=AsyncMock()),
        ):
            # Always return a function call → hits MAX_TOOL_CALLS
            mock_client.return_value.models.generate_content.return_value = make_fn_candidate("LOOP", {})
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


def test_agent_returns_error_message_on_gemini_failure():
    async def run():
        config = make_config()

        class FakeAPIError(Exception):
            code = 503

        with (
            patch("bot.agent.genai.Client") as mock_client,
            patch("bot.agent.get_toolset", return_value=Mock()),
            patch("bot.agent.get_gemini_tools", return_value=[]),
            patch("bot.agent.load_history", new=AsyncMock(return_value=[])),
            patch("bot.agent.append_turn", new=AsyncMock()),
            patch("bot.agent.errors.APIError", FakeAPIError),
        ):
            mock_client.return_value.models.generate_content.side_effect = FakeAPIError()
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


def test_history_to_contents_reconstructs_tool_pairs():
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
    contents = _history_to_contents(rows)
    # user, model text, model fn_call, tool response
    assert len(contents) == 4
    assert contents[0].role == "user"
    assert contents[1].role == "model"
    assert contents[2].role == "model"
    assert contents[3].role == "tool"


def test_agent_uses_override_timezone():
    async def run():
        config = make_config()

        with (
            patch("bot.agent.genai.Client") as mock_client,
            patch("bot.agent.get_toolset", return_value=Mock()),
            patch("bot.agent.get_gemini_tools", return_value=[]),
            patch("bot.agent.load_history", new=AsyncMock(return_value=[])),
            patch("bot.agent.append_turn", new=AsyncMock()),
        ):
            mock_client.return_value.models.generate_content.return_value = make_text_candidate("ok")
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
            call_config = mock_client.return_value.models.generate_content.call_args.kwargs["config"]
            assert "America/Los_Angeles" in call_config.system_instruction

    asyncio.run(run())
