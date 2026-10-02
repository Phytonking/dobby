"""Real Composio session + real tool schemas, asserted against the actual
live API response — not a shape we assumed and hand-wrote into a stub.

Catches exactly the two bugs that a live `make chat` session surfaced and
nothing else in the suite could: an unscoped toolkit pulling in that
toolkit's entire API surface, and Composio's OpenAI-provider output carrying
a field this particular model endpoint's request validation rejects.
"""

import os

from bot.tools import TOOLS, create_composio_session, get_openai_tools

ENTITY_ID = os.environ.get("COMPOSIO_ENTITY_ID", "dobby")
EXPECTED_TOOL_NAMES = {slug for slugs in TOOLS.values() for slug in slugs}


def _live_tools():
    _, session = create_composio_session(os.environ["COMPOSIO_API_KEY"], ENTITY_ID)
    return get_openai_tools(session)


def test_tool_scope_matches_the_explicit_allowlist_not_a_toolkits_firehose():
    """toolkits=[...] with no per-tool filter preloads everything in each
    toolkit — github alone is ~875 actions. This must stay small and exact."""
    tools = _live_tools()
    names = {t["function"]["name"] for t in tools}

    assert names == EXPECTED_TOOL_NAMES, (
        f"live Composio tool set drifted from bot.tools.TOOLS — "
        f"missing={EXPECTED_TOOL_NAMES - names} unexpected={names - EXPECTED_TOOL_NAMES}"
    )
    assert len(tools) < 20, f"got {len(tools)} tools — did TOOLS widen back toward toolkits=[...]?"


def test_no_tool_carries_a_null_strict_field():
    """Composio's OpenAIProvider always sets function.strict = None. OpenAI's
    API tolerates that; the Modal/vLLM endpoint's request schema types strict
    as a plain bool and 400s on null for every tool, every request — this
    exact field is why the fix in get_openai_tools exists."""
    tools = _live_tools()

    for tool in tools:
        assert "strict" not in tool["function"] or isinstance(tool["function"]["strict"], bool), (
            f"{tool['function']['name']} has strict={tool['function'].get('strict')!r}"
        )


def test_each_tool_has_a_usable_schema():
    tools = _live_tools()

    for tool in tools:
        fn = tool["function"]
        assert tool["type"] == "function"
        assert fn["name"]
        assert isinstance(fn.get("parameters"), dict)
