"""Tests for bot/composio.py — schema conversion and response digging. Composio itself is faked."""

import asyncio
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest
from composio_client import AuthenticationError, PermissionDeniedError

from bot.composio import declarations_for, execute_tool, find_key, run_action
from bot.AIModels import gemini_schema
from bot.models import ConfigError


def test_gemini_schema_keeps_only_supported_keys_recursively():
    schema = {
        "title": "Params",
        "type": "object",
        "properties": {
            "summary": {"type": "string", "title": "Summary", "default": "", "examples": ["x"]},
            "attendees": {"type": "array", "items": {"type": "string", "title": "Email"}},
            "when": {"type": ["string", "null"], "description": "ISO time"},
            "nested": {"properties": {"a": {"type": "integer"}}, "$defs": {}},
        },
        "required": ["summary"],
        "$defs": {"Thing": {}},
    }
    assert gemini_schema(schema) == {
        "type": "object",
        "properties": {
            "summary": {"type": "string"},
            "attendees": {"type": "array", "items": {"type": "string"}},
            "when": {"type": "string", "nullable": True, "description": "ISO time"},
            "nested": {"type": "object", "properties": {"a": {"type": "integer"}}},
        },
        "required": ["summary"],
    }


def fake_toolset(models=None, error=None):
    toolset = Mock()
    if error:
        toolset.tools.get_raw_composio_tools.side_effect = error
    else:
        toolset.tools.get_raw_composio_tools.return_value = models or []
    return toolset


def model(slug, **props):
    """A composio ``Tool``: ``input_parameters`` is a plain dict on the current SDK."""
    params = {"type": "object", "properties": props, "title": "P"}
    return SimpleNamespace(slug=slug, description=f"{slug} desc", input_parameters=params)


def test_declarations_for_converts_models_and_skips_unknown_actions(caplog):
    toolset = fake_toolset([model("NOTION_SEARCH_NOTION_PAGE", query={"type": "string", "title": "Q"})])
    decls = declarations_for(toolset, ("NOTION_SEARCH_NOTION_PAGE", "NOTION_NOT_A_THING"))
    toolset.tools.get_raw_composio_tools.assert_called_once_with(
        tools=["NOTION_SEARCH_NOTION_PAGE", "NOTION_NOT_A_THING"]
    )
    assert [d.name for d in decls] == ["NOTION_SEARCH_NOTION_PAGE"]
    assert decls[0].parameters["properties"]["query"]["type"] == "string"
    assert "composio_action_unknown action=NOTION_NOT_A_THING" in caplog.text


def test_declarations_for_accepts_pydantic_style_parameters():
    """Older/alternate schema shapes still expose ``model_dump``; the bridge unwraps them."""
    tool = model("NOTION_X")
    tool.input_parameters = SimpleNamespace(
        model_dump=lambda exclude_none=True: {"type": "object", "properties": {"q": {"type": "string"}}}
    )
    decls = declarations_for(fake_toolset([tool]), ("NOTION_X",))
    assert decls[0].parameters["properties"]["q"]["type"] == "string"


def test_declarations_for_survives_composio_being_down(caplog):
    assert declarations_for(fake_toolset(error=RuntimeError("down")), ("X",)) == []
    assert "composio_schemas_failed" in caplog.text
    assert declarations_for(fake_toolset(), ()) == []  # nothing requested → no call


@pytest.mark.parametrize("error_class,status", [(AuthenticationError, 401), (PermissionDeniedError, 403)])
def test_rejected_project_credential_stops_startup_without_leaking_secrets(error_class, status, caplog):
    response = httpx.Response(
        status,
        request=httpx.Request("GET", "https://backend.composio.dev/api/v3.1/tools"),
        headers={"x-request-id": "request-123"},
    )
    error = error_class("secret-must-not-be-logged", response=response, body={"error": "secret"})
    with pytest.raises(ConfigError, match="Composio rejected"):
        declarations_for(fake_toolset(error=error), ("NOTION_SEARCH_NOTION_PAGE",))
    assert f"status={status} request_id=request-123" in caplog.text
    assert "composio_auth_rejected" in caplog.text
    assert "secret" not in caplog.text


def test_execute_tool_wraps_success_and_failure():
    toolset = Mock()
    toolset.tools.execute.return_value = {"successful": True, "data": {"id": "1"}, "error": None}
    assert execute_tool(toolset, "A", {"x": 1}, "dobby") == {"success": True, "data": {"id": "1"}}
    toolset.tools.execute.assert_called_once_with(slug="A", arguments={"x": 1}, user_id="dobby")

    # Composio reports tool-level failure in the envelope, not as an exception.
    toolset.tools.execute.return_value = {"successful": False, "data": {}, "error": "boom"}
    assert execute_tool(toolset, "A", {}, "dobby") == {"success": False, "error": "boom"}

    toolset.tools.execute.return_value = {"successful": False, "data": {}, "error": None}
    assert execute_tool(toolset, "A", {}, "dobby") == {
        "success": False,
        "error": "Composio tool execution failed",
    }

    toolset.tools.execute.side_effect = RuntimeError("nope")
    assert execute_tool(toolset, "A", {}, "dobby") == {"success": False, "error": "nope"}


def test_run_action_runs_off_the_event_loop():
    toolset = Mock()
    toolset.tools.execute.return_value = {"successful": True, "data": "ok", "error": None}
    assert asyncio.run(run_action(toolset, "A", {}, "dobby")) == {"success": True, "data": "ok"}


def test_find_key_digs_through_envelopes():
    data = {"response_data": {"data": [{"id": "", "creation_id": "17"}]}, "successful": True}
    assert find_key(data, "id", "creation_id") == "17"
    assert find_key(data, "missing") is None
    assert find_key([{"sub": "abc"}], "sub") == "abc"
