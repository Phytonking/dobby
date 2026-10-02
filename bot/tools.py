# Uses composio>=0.21.0 (v3 API). The old composio-core 0.7.x is dead (v1 API returns 410).
import logging

log = logging.getLogger("tools")

# Composio exposes each toolkit's *entire* API surface as individual tools —
# github alone is ~875 (every REST endpoint: codespaces, billing, org admin,
# etc). toolkits=[...] with no filter preloads all of it: 975 tool schemas in
# one request, which the model endpoint rejects outright (400). Scope down to
# exactly what the system prompt promises and nothing else. Keep this in sync
# with scripts/eval_cases.py's STUB_TOOLS — same slugs, so eval tests the set
# that's actually live.
TOOLS = {
    "googlecalendar": [
        "GOOGLECALENDAR_CREATE_EVENT",
        "GOOGLECALENDAR_FIND_EVENT",
        "GOOGLECALENDAR_DELETE_EVENT",
    ],
    "github": [
        "GITHUB_CREATE_AN_ISSUE",
        "GITHUB_LIST_REPOSITORY_ISSUES",
    ],
    "notion": [
        "NOTION_CREATE_NOTION_PAGE",
        "NOTION_SEARCH_NOTION_PAGE",
    ],
}


def create_composio_session(api_key: str, entity_id: str):
    from composio import SESSION_PRESET_DIRECT_TOOLS, Composio

    client = Composio(api_key=api_key)
    # Without a session_preset, sessions expose router/search meta tools
    # instead of the toolkit actions themselves — session.tools() would never
    # contain GOOGLECALENDAR_CREATE_EVENT etc. direct_tools skips that
    # indirection since every tool we need is known upfront.
    session = client.sessions.create(
        user_id=entity_id,
        tools=TOOLS,
        session_preset=SESSION_PRESET_DIRECT_TOOLS,
    )
    return client, session


def get_openai_tools(session) -> list[dict]:
    # Composio(api_key=...) defaults to OpenAIProvider, so session.tools()
    # already returns the {"type": "function", "function": {...}} shape the
    # chat completions API's `tools=` expects — no reshaping needed, except:
    # Composio's wrap_tool() always sets function.strict = None (meaning
    # "unspecified" to OpenAI's own API, which tolerates null there). This
    # Modal/vLLM endpoint's request schema types that field as a plain bool
    # and 400s on null — confirmed directly: "7 validation errors ... Input
    # should be a valid boolean" for every single tool, every single time.
    # Dropping the key entirely (vs. coercing to False) lets the server apply
    # its own default instead of us guessing one.
    tools = []
    for tool in session.tools():
        tool = dict(tool)
        if "function" in tool and tool["function"].get("strict") is None:
            tool["function"] = {k: v for k, v in tool["function"].items() if k != "strict"}
        tools.append(tool)
    return tools


def execute_tool(session, tool_name: str, params: dict) -> dict:
    try:
        result = session.execute(tool_name, arguments=params)
        data = result if isinstance(result, dict) else getattr(result, "data", str(result))
        return {"success": True, "data": data}
    except Exception as exc:
        log.warning("tool_execute_failed tool=%s error=%s", tool_name, type(exc).__name__)
        return {"success": False, "error": str(exc)}
