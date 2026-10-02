"""Tests for bot/tools.py — Composio session setup and tool execution.

Regression coverage for things confirmed against the installed composio SDK
(v0.24) and, for the strict-field and tool-count issues, the real live
Composio + Modal endpoint (caught by chatting with the real bot, not eval —
eval's STUB_TOOLS never exercised real Composio output):

1. sessions.create() without session_preset=SESSION_PRESET_DIRECT_TOOLS
   exposes router/search meta tools instead of the toolkit actions themselves
   (GOOGLECALENDAR_CREATE_EVENT, etc. never appear in session.tools()).
2. Composio(api_key=...) defaults to OpenAIProvider, so session.tools() is
   already {"type": "function", "function": {...}} — exactly what a chat
   completions `tools=` parameter expects. get_openai_tools is mostly a
   passthrough (see #4 for the one exception).
3. toolkits=[...] with no per-tool filter preloads a toolkit's *entire* API
   surface — github alone is ~875 tools. Unfiltered, that's 975 tool schemas
   in one request. tools={toolkit: [slug, ...]} scopes to exactly what's
   needed instead.
4. Composio's OpenAIProvider.wrap_tool() always sets function.strict = None.
   OpenAI's own API tolerates that; this Modal/vLLM endpoint's request schema
   types strict as a plain bool and 400s on null — confirmed directly against
   the live endpoint ("N validation errors ... Input should be a valid
   boolean", one per tool, every time). get_openai_tools drops the key
   entirely when it's None rather than guessing a replacement value.
"""

from unittest.mock import Mock, patch

from bot.tools import TOOLS, create_composio_session, execute_tool, get_openai_tools


# ---------------------------------------------------------------------------
# create_composio_session
# ---------------------------------------------------------------------------


def test_create_composio_session_requests_direct_tools_preset():
    with patch("composio.Composio") as mock_composio_cls:
        mock_client = mock_composio_cls.return_value
        mock_session = mock_client.sessions.create.return_value

        client, session = create_composio_session("api-key", "entity-1")

        assert client is mock_client
        assert session is mock_session
        mock_composio_cls.assert_called_once_with(api_key="api-key")
        _, kwargs = mock_client.sessions.create.call_args
        assert kwargs["user_id"] == "entity-1"
        # Per-tool filter, not toolkits=[...] — toolkits alone preloads a
        # toolkit's entire API surface (~875 tools for github alone).
        assert kwargs["tools"] == TOOLS
        assert "toolkits" not in kwargs
        # direct_tools — the actual string value SESSION_PRESET_DIRECT_TOOLS
        # resolves to in the installed SDK; a plain string keeps this test
        # from silently passing if the import is dropped.
        assert kwargs["session_preset"] == "direct_tools"


def test_tools_scope_is_a_short_explicit_allowlist():
    """Guards against someone widening TOOLS back to toolkits=[...] (or
    forgetting a toolkit) without noticing the request becomes huge again."""
    assert set(TOOLS.keys()) == {"googlecalendar", "github", "notion"}
    total = sum(len(v) for v in TOOLS.values())
    assert total < 20, f"TOOLS grew to {total} entries — was this meant to be this broad?"


# ---------------------------------------------------------------------------
# get_openai_tools
# ---------------------------------------------------------------------------


def test_get_openai_tools_passes_through_session_tools():
    openai_shaped = [
        {
            "type": "function",
            "function": {
                "name": "GOOGLECALENDAR_CREATE_EVENT",
                "description": "Create a calendar event",
                "parameters": {"type": "object", "properties": {"summary": {"type": "string"}}},
            },
        }
    ]
    session = Mock()
    session.tools.return_value = openai_shaped

    assert get_openai_tools(session) == openai_shaped


def test_get_openai_tools_coerces_non_list_iterable():
    session = Mock()
    session.tools.return_value = iter([{"type": "function", "function": {"name": "X"}}])

    result = get_openai_tools(session)

    assert isinstance(result, list)
    assert result == [{"type": "function", "function": {"name": "X"}}]


def test_get_openai_tools_empty_session_returns_empty_list():
    session = Mock()
    session.tools.return_value = []

    assert get_openai_tools(session) == []


def test_get_openai_tools_strips_null_strict_field():
    """The exact shape Composio's OpenAIProvider actually returns — strict is
    always present and always None. Sent as-is, the Modal endpoint 400s:
    "Input should be a valid boolean" for every tool, every request."""
    session = Mock()
    session.tools.return_value = [
        {
            "type": "function",
            "function": {
                "name": "GOOGLECALENDAR_CREATE_EVENT",
                "description": "Create a calendar event",
                "parameters": {"type": "object", "properties": {}},
                "strict": None,
            },
        }
    ]

    result = get_openai_tools(session)

    assert "strict" not in result[0]["function"]
    assert result[0]["function"]["name"] == "GOOGLECALENDAR_CREATE_EVENT"


def test_get_openai_tools_preserves_real_strict_value():
    """Only None is stripped — an explicit True/False is a real caller
    decision, not Composio's placeholder, and must pass through untouched."""
    session = Mock()
    session.tools.return_value = [
        {"type": "function", "function": {"name": "X", "strict": True}},
    ]

    result = get_openai_tools(session)

    assert result[0]["function"]["strict"] is True


def test_get_openai_tools_does_not_mutate_session_tools_output():
    original = [{"type": "function", "function": {"name": "X", "strict": None}}]
    session = Mock()
    session.tools.return_value = original

    get_openai_tools(session)

    assert original[0]["function"]["strict"] is None


# ---------------------------------------------------------------------------
# execute_tool
# ---------------------------------------------------------------------------


def test_execute_tool_wraps_dict_result():
    session = Mock()
    session.execute.return_value = {"id": "evt_1"}

    result = execute_tool(session, "GOOGLECALENDAR_CREATE_EVENT", {"summary": "Sync"})

    session.execute.assert_called_once_with("GOOGLECALENDAR_CREATE_EVENT", arguments={"summary": "Sync"})
    assert result == {"success": True, "data": {"id": "evt_1"}}


def test_execute_tool_extracts_data_attribute_from_non_dict_result():
    session = Mock()
    session.execute.return_value = Mock(data={"id": "evt_2"})

    result = execute_tool(session, "GOOGLECALENDAR_CREATE_EVENT", {})

    assert result == {"success": True, "data": {"id": "evt_2"}}


def test_execute_tool_reports_failure_without_raising():
    session = Mock()
    session.execute.side_effect = RuntimeError("rate limited")

    result = execute_tool(session, "GOOGLECALENDAR_CREATE_EVENT", {})

    assert result == {"success": False, "error": "rate limited"}
