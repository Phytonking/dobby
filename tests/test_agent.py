"""Tests for the generic Gemini loop (bot/agent.py). Gemini, Composio and the DB are mocked."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import httpx
import pytest
from google.genai import errors
from bot import AIModels as types

from bot.agent import Agent, AgentResult, CONTEXT_INTRO
from bot.integrations import ToolRegistry
from bot.integrations.base import LocalTool, PendingAction


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_config():
    return SimpleNamespace(
        gemini_key="fake-gemini",
        composio_key="fake-composio",
        composio_entity="dobby",
        model="gemini-test",
        model_backup="gemini-3.1-flash-lite",
        timezone="UTC",
    )


def make_text_candidate(text):
    part = SimpleNamespace(text=text, function_call=None)
    return SimpleNamespace(candidates=[SimpleNamespace(content=SimpleNamespace(role="model", parts=[part]))])


def make_fn_candidate(tool_name, args):
    # SimpleNamespace, not Mock: `name` is special on Mock.
    part = SimpleNamespace(text="", function_call=SimpleNamespace(name=tool_name, args=args))
    return SimpleNamespace(candidates=[SimpleNamespace(content=SimpleNamespace(role="model", parts=[part]))])


def declaration(name):
    return types.FunctionDeclaration(
        name=name, description=name, parameters={"type": "object", "properties": {}}
    )


def registry(*, composio=("GOOGLECALENDAR_CREATE_EVENT",), local=(), prompt=""):
    decls = [declaration(n) for n in composio] + [t.declaration for t in local]
    return ToolRegistry(
        tools=[types.Tool(function_declarations=decls)] if decls else [],
        local={t.name: t for t in local},
        owner={**{n: "google_calendar" for n in composio}, **{t.name: "test" for t in local}},
        prompt=prompt,
    )


def run_kwargs(**overrides):
    kwargs = dict(request="do something", guild_id="10", channel_id="40", discord_user_id="1")
    kwargs.update(overrides)
    return kwargs


def gen(client):
    """The async Gemini call the agent awaits (`client.aio.models.generate_content`)."""
    models = client.return_value.aio.models
    if not isinstance(models.generate_content, AsyncMock):
        models.generate_content = AsyncMock()
    return models.generate_content


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_agent_returns_text_on_first_call():
    async def run():
        with patch("bot.AIModels.genai.Client") as client, patch("bot.agent.record_action", new=AsyncMock()):
            gen(client).return_value = make_text_candidate("Event created.")
            result = await Agent(make_config(), registry()).run(session=AsyncMock(), **run_kwargs())
        assert result == AgentResult("Event created.", [])
        assert gen(client).await_count == 1
        assert gen(client).await_args.kwargs["model"] == "gemini-test"
        assert client.call_args.kwargs["http_options"].retry_options.attempts == 1

    asyncio.run(run())


@pytest.mark.parametrize("code", [408, 429, 500, 502, 503, 504])
def test_transient_error_switches_to_backup_on_first_failure(code):
    async def run():
        with patch("bot.AIModels.genai.Client") as client:
            gen(client).side_effect = [errors.APIError(code, {}), make_text_candidate("Recovered.")]
            result = await Agent(make_config(), registry()).run(session=AsyncMock(), **run_kwargs())
        assert result.text == "Recovered."
        calls = gen(client).await_args_list
        assert [c.kwargs["model"] for c in calls] == ["gemini-test", "gemini-3.1-flash-lite"]
        assert calls[0].kwargs["contents"] == calls[1].kwargs["contents"]
        assert calls[0].kwargs["config"] == calls[1].kwargs["config"]

    asyncio.run(run())


@pytest.mark.parametrize("error", [httpx.ReadTimeout("timeout"), TimeoutError()])
def test_timeout_uses_backup(error):
    async def run():
        with patch("bot.AIModels.genai.Client") as client:
            gen(client).side_effect = [error, make_text_candidate("Recovered.")]
            result = await Agent(make_config(), registry()).run(session=AsyncMock(), **run_kwargs())
        assert result.text == "Recovered."
        assert gen(client).await_args.kwargs["model"] == "gemini-3.1-flash-lite"

    asyncio.run(run())


@pytest.mark.parametrize("code", [400, 401, 403, 404])
def test_permanent_error_does_not_try_backup(code):
    async def run():
        with patch("bot.AIModels.genai.Client") as client:
            gen(client).side_effect = errors.APIError(code, {})
            result = await Agent(make_config(), registry()).run(session=AsyncMock(), **run_kwargs())
        assert gen(client).await_count == 1
        assert "admin" in result.text.lower()

    asyncio.run(run())


@pytest.mark.parametrize("backup,expected_calls", [("", 1), ("gemini-test", 1), ("backup", 2)])
def test_fallback_is_bounded_even_when_both_models_fail(backup, expected_calls):
    async def run():
        cfg = make_config()
        cfg.model_backup = backup
        with patch("bot.AIModels.genai.Client") as client:
            gen(client).side_effect = errors.APIError(503, {})
            result = await Agent(cfg, registry()).run(session=AsyncMock(), **run_kwargs())
        assert gen(client).await_count == expected_calls
        assert "magic" in result.text.lower()

    asyncio.run(run())


def test_fallback_preserves_completed_tools_pending_actions_and_resets_for_next_request():
    pending = PendingAction("linkedin", "LinkedIn post", "draft", AsyncMock())
    handler = AsyncMock(side_effect=lambda ctx, params: ctx.queue(pending))
    tool = LocalTool(declaration("draft_linkedin_post"), handler)

    async def run():
        with patch("bot.AIModels.genai.Client") as client, patch("bot.agent.record_action", new=AsyncMock()):
            gen(client).side_effect = [
                make_fn_candidate("draft_linkedin_post", {"text": "draft"}),
                errors.APIError(503, {}),
                make_fn_candidate("GOOGLECALENDAR_FIND_EVENT", {}),
                make_text_candidate("Please confirm."),
                make_text_candidate("Next request."),
            ]
            agent = Agent(make_config(), registry(composio=("GOOGLECALENDAR_FIND_EVENT",), local=(tool,)))
            with patch("bot.agent.run_action", new=AsyncMock(return_value={"success": True})) as action:
                result = await agent.run(session=AsyncMock(), **run_kwargs())
                await agent.run(session=AsyncMock(), **run_kwargs())
            assert action.await_count == 1
        assert result.pending == [pending]
        assert result.text == "Please confirm."
        handler.assert_awaited_once()
        pending.execute.assert_not_awaited()
        assert [c.kwargs["model"] for c in gen(client).await_args_list] == [
            "gemini-test",
            "gemini-test",
            "gemini-3.1-flash-lite",
            "gemini-3.1-flash-lite",
            "gemini-test",
        ]
        history = gen(client).await_args_list[2].kwargs["contents"]
        assert any(getattr(p, "function_response", None) for c in history for p in c.parts)

    asyncio.run(run())


def test_composio_tool_runs_under_service_entity_and_is_recorded():
    async def run():
        run_action = AsyncMock(return_value={"success": True, "data": {"id": "abc"}})
        record = AsyncMock()
        with (
            patch("bot.AIModels.genai.Client") as client,
            patch("bot.agent.run_action", run_action),
            patch("bot.agent.record_action", record),
        ):
            gen(client).side_effect = [
                make_fn_candidate("GOOGLECALENDAR_CREATE_EVENT", {"summary": "Sync"}),
                make_text_candidate("Meeting created."),
            ]
            agent = Agent(make_config(), registry())
            agent.toolset = Mock()
            session = AsyncMock()
            result = await agent.run(session=session, **run_kwargs())

        assert result.text == "Meeting created."
        run_action.assert_awaited_once_with(
            agent.toolset, "GOOGLECALENDAR_CREATE_EVENT", {"summary": "Sync"}, "dobby"
        )
        kwargs = record.await_args.kwargs
        assert kwargs["tool"] == "GOOGLECALENDAR_CREATE_EVENT" and kwargs["status"] == "ok"
        assert "input" not in kwargs and "output" not in kwargs  # metadata only
        session.commit.assert_awaited()

    asyncio.run(run())


def test_failed_tool_is_recorded_as_error_and_unknown_tool_is_refused():
    async def run():
        record = AsyncMock()
        with (
            patch("bot.AIModels.genai.Client") as client,
            patch("bot.agent.run_action", new=AsyncMock(return_value={"success": False, "error": "boom"})),
            patch("bot.agent.record_action", record),
        ):
            gen(client).side_effect = [
                make_fn_candidate("GOOGLECALENDAR_CREATE_EVENT", {}),
                make_fn_candidate("GITHUB_CREATE_ISSUE", {}),  # not in the registry
                make_text_candidate("Sorry."),
            ]
            await Agent(make_config(), registry()).run(session=AsyncMock(), **run_kwargs())
        statuses = [c.kwargs["status"] for c in record.await_args_list]
        assert statuses == ["error", "error"]

    asyncio.run(run())


def test_local_tool_runs_in_process_and_can_queue_a_pending_action():
    handler = AsyncMock(
        side_effect=lambda ctx, params: ctx.queue(
            PendingAction("linkedin", "LinkedIn post", params["text"], AsyncMock())
        )
    )
    tool = LocalTool(declaration("draft_linkedin_post"), handler)

    async def run():
        with (
            patch("bot.AIModels.genai.Client") as client,
            patch("bot.agent.run_action", new=AsyncMock()) as run_action,
            patch("bot.agent.record_action", new=AsyncMock()),
        ):
            gen(client).side_effect = [
                make_fn_candidate("draft_linkedin_post", {"text": "hello"}),
                make_text_candidate("Please confirm."),
            ]
            result = await Agent(make_config(), registry(local=(tool,))).run(
                session=AsyncMock(), **run_kwargs()
            )
            contents = gen(client).call_args.kwargs["contents"]

        run_action.assert_not_awaited()
        handler.assert_awaited_once()
        assert [p.preview for p in result.pending] == ["hello"]
        assert contents[-1].parts[0].function_response.response["queued"] is True

    asyncio.run(run())


def test_local_tool_exception_becomes_an_error_result():
    tool = LocalTool(declaration("explode"), AsyncMock(side_effect=RuntimeError("x")))

    async def run():
        record = AsyncMock()
        with patch("bot.AIModels.genai.Client") as client, patch("bot.agent.record_action", record):
            gen(client).side_effect = [
                make_fn_candidate("explode", {}),
                make_text_candidate("ok"),
            ]
            await Agent(make_config(), registry(local=(tool,))).run(session=AsyncMock(), **run_kwargs())
        assert record.await_args.kwargs["status"] == "error"

    asyncio.run(run())


def test_agent_stops_after_max_tool_calls():
    async def run():
        with (
            patch("bot.AIModels.genai.Client") as client,
            patch("bot.agent.run_action", new=AsyncMock(return_value={"success": True, "data": {}})),
            patch("bot.agent.record_action", new=AsyncMock()),
        ):
            gen(client).return_value = make_fn_candidate("GOOGLECALENDAR_CREATE_EVENT", {})
            result = await Agent(make_config(), registry()).run(session=AsyncMock(), **run_kwargs())
        assert "smaller" in result.text.lower()

    asyncio.run(run())


def test_agent_returns_error_message_on_gemini_failure():
    async def run():
        class FakeAPIError(Exception):
            code = 503
            status = "UNAVAILABLE"

        with (
            patch("bot.AIModels.genai.Client") as client,
            patch("bot.agent.record_action", new=AsyncMock()),
            patch("bot.AIModels.errors.APIError", FakeAPIError),
        ):
            gen(client).side_effect = FakeAPIError()
            result = await Agent(make_config(), registry()).run(session=AsyncMock(), **run_kwargs())
        assert "magic" in result.text.lower()  # Dobby's phrasing for a transient outage

    asyncio.run(run())


def test_channel_context_precedes_the_request_as_untrusted_data():
    async def run():
        with patch("bot.AIModels.genai.Client") as client, patch("bot.agent.record_action", new=AsyncMock()):
            gen(client).return_value = make_text_candidate("ok")
            await Agent(make_config(), registry()).run(
                session=AsyncMock(),
                **run_kwargs(request="book it", context=["[10:00] Maya: lunch friday?", "[10:01] Leo: sure"]),
            )
            contents = gen(client).call_args.kwargs["contents"]
        assert len(contents) == 2
        assert contents[0].parts[0].text == CONTEXT_INTRO + "[10:00] Maya: lunch friday?\n[10:01] Leo: sure"
        assert contents[1].parts[0].text == "book it"

    asyncio.run(run())


def test_system_prompt_includes_integration_guidance_people_and_timezone():
    async def run():
        with patch("bot.AIModels.genai.Client") as client, patch("bot.agent.record_action", new=AsyncMock()):
            gen(client).return_value = make_text_candidate("ok")
            await Agent(make_config(), registry(prompt="Notion: search before creating.")).run(
                session=AsyncMock(),
                **run_kwargs(
                    known_people=[
                        {"display_name": "Maya Chen", "calendar_email": "maya@uw.edu"},
                        {"display_name": "Sam", "calendar_email": None},
                    ],
                    timezone="America/Los_Angeles",
                ),
            )
            config = gen(client).call_args.kwargs["config"]
        system = config.system_instruction
        assert "Notion: search before creating." in system
        assert "Maya Chen — maya@uw.edu" in system and "Sam — no email on file" in system
        assert "America/Los_Angeles" in system
        assert config.tools == registry().tools or config.tools is not None

    asyncio.run(run())


def test_no_tools_means_none_is_passed_to_gemini():
    async def run():
        with patch("bot.AIModels.genai.Client") as client, patch("bot.agent.record_action", new=AsyncMock()):
            gen(client).return_value = make_text_candidate("ok")
            await Agent(make_config(), registry(composio=())).run(session=AsyncMock(), **run_kwargs())
            config = gen(client).call_args.kwargs["config"]
        assert config.tools is None

    asyncio.run(run())


def test_function_responses_are_sent_with_role_user():
    """Regression: role="tool" is rejected by the Gemini API with 400 INVALID_ARGUMENT.

    Only "user" and "model" are valid roles, so every request that called a tool failed while
    plain chat kept working. Pin the role that carries function results back.
    """

    async def run():
        with (
            patch("bot.AIModels.genai.Client") as client,
            patch("bot.agent.run_action", new=AsyncMock(return_value={"success": True, "data": {}})),
            patch("bot.agent.record_action", new=AsyncMock()),
        ):
            gen(client).side_effect = [
                make_fn_candidate("GOOGLECALENDAR_CREATE_EVENT", {"summary": "Sync"}),
                make_text_candidate("Dobby has done it!"),
            ]
            agent = Agent(make_config(), registry())
            agent.toolset = Mock()
            await agent.run(session=AsyncMock(), **run_kwargs())
            contents = gen(client).call_args.kwargs["contents"]

        tool_turn = contents[-1]
        assert tool_turn.parts[0].function_response is not None
        assert tool_turn.role == "user"
        assert {c.role for c in contents} <= {"user", "model"}

    asyncio.run(run())


def test_rejected_request_is_not_reported_as_a_transient_outage():
    """A 400 is Dobby's own bad request: telling the user to retry would be wrong."""

    async def run():
        class FakeAPIError(Exception):
            code = 400
            status = "INVALID_ARGUMENT"

        with (
            patch("bot.AIModels.genai.Client") as client,
            patch("bot.agent.record_action", new=AsyncMock()),
            patch("bot.AIModels.errors.APIError", FakeAPIError),
        ):
            gen(client).side_effect = FakeAPIError()
            result = await Agent(make_config(), registry()).run(session=AsyncMock(), **run_kwargs())

        assert "admin" in result.text.lower()  # points at the logs, not at retrying

    asyncio.run(run())


def test_candidate_without_parts_answers_instead_of_crashing():
    """MAX_TOKENS / SAFETY finishes hand back content with parts=None."""

    async def run():
        empty = SimpleNamespace(
            candidates=[SimpleNamespace(content=SimpleNamespace(parts=None), finish_reason="MAX_TOKENS")]
        )
        with patch("bot.AIModels.genai.Client") as client, patch("bot.agent.record_action", new=AsyncMock()):
            gen(client).return_value = empty
            result = await Agent(make_config(), registry()).run(session=AsyncMock(), **run_kwargs())

        assert "Dobby" in result.text

    asyncio.run(run())


def test_system_prompt_carries_the_dobby_voice():
    async def run():
        with patch("bot.AIModels.genai.Client") as client, patch("bot.agent.record_action", new=AsyncMock()):
            gen(client).return_value = make_text_candidate("ok")
            await Agent(make_config(), registry()).run(session=AsyncMock(), **run_kwargs())
            system = gen(client).call_args.kwargs["config"].system_instruction
        assert "third person" in system
        assert "house-elf" in system
        # No gendered honorifics: Dobby must not guess anything about the requester.
        assert '"sir"' in system and "Never" in system

    asyncio.run(run())
