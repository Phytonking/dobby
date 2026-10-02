"""Tests for the integration registry (bot/integrations/__init__.py)."""

from unittest.mock import Mock, patch

from bot import AIModels as types

from bot.integrations import INTEGRATIONS, build_registry
from bot.integrations.base import Integration, LocalTool


def decl(name):
    return types.FunctionDeclaration(
        name=name, description=name, parameters={"type": "object", "properties": {}}
    )


def test_every_integration_has_a_key_label_and_commands():
    keys = [i.key for i in INTEGRATIONS]
    assert keys == ["google_calendar", "notion", "instagram", "linkedin"]
    for integration in INTEGRATIONS:
        assert integration.label and integration.register_commands and integration.help_lines


def test_publishing_integrations_expose_no_direct_composio_actions():
    by_key = {i.key: i for i in INTEGRATIONS}
    assert by_key["instagram"].actions == () and by_key["linkedin"].actions == ()
    assert "draft_linkedin_post" in {t.name for t in by_key["linkedin"].local_tools}
    assert {"list_instagram_posts", "draft_instagram_post", "draft_instagram_story"} == {
        t.name for t in by_key["instagram"].local_tools
    }


def test_build_registry_merges_composio_and_local_tools_with_owners():
    local = LocalTool(decl("local_one"), Mock())
    integrations = (
        Integration(key="a", label="A", app="notion", actions=("A_ONE", "A_TWO"), prompt="A rules."),
        Integration(key="b", label="B", app="notion", local_tools=(local,), prompt="  "),
    )
    with patch(
        "bot.integrations.declarations_for", side_effect=lambda ts, actions: [decl(n) for n in actions]
    ) as df:
        registry = build_registry("toolset", integrations)
    df.assert_any_call("toolset", ("A_ONE", "A_TWO"))
    names = [d.name for d in registry.tools[0].function_declarations]
    assert names == ["A_ONE", "A_TWO", "local_one"]
    assert registry.owner == {"A_ONE": "a", "A_TWO": "a", "local_one": "b"}
    assert registry.local == {"local_one": local}
    assert registry.prompt == "A rules."


def test_build_registry_with_nothing_yields_no_tools():
    with patch("bot.integrations.declarations_for", return_value=[]):
        registry = build_registry("toolset", ())
    assert registry.tools == [] and registry.local == {} and registry.prompt == ""
