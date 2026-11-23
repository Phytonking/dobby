"""Every curated Composio action still resolves, against the real API at the pinned versions.

A renamed action or a stale TOOLKIT_VERSIONS pin otherwise only surfaces at bot startup as
`composio_action_unknown` / `composio_schemas_failed` with the tool silently missing.
"""

import os

import pytest

from bot.composio import declarations_for, get_toolset
from bot.integrations import INTEGRATIONS


@pytest.fixture(scope="module")
def toolset():
    return get_toolset(os.environ["COMPOSIO_API_KEY"])


@pytest.mark.parametrize("integration", INTEGRATIONS, ids=lambda i: i.key)
def test_every_curated_action_resolves(toolset, integration):
    declarations = declarations_for(toolset, integration.actions)

    resolved = {d.name for d in declarations}
    assert resolved == set(integration.actions), f"unknown actions: {set(integration.actions) - resolved}"
    for declaration in declarations:
        assert declaration.parameters is None or isinstance(declaration.parameters, dict)


def test_instagram_actions_called_inside_local_tools_resolve(toolset):
    """Instagram exposes no direct actions to the model, so its publish and DM actions are checked here."""
    from bot.integrations.instagram import dms, publish

    actions = (publish.LIST_MEDIA, publish.CREATE_CONTAINER, publish.PUBLISH, *dms.DM_ACTIONS)
    resolved = {d.name for d in declarations_for(toolset, actions)}
    assert resolved == set(actions), f"unknown actions: {set(actions) - resolved}"
