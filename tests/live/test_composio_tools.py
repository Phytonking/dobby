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
