"""Notion: the team workspace Dobby searches and writes notes into."""

from ..base import Integration
from . import commands

# Curated: what the model may call directly. Verify names against Composio's catalog at deploy time.
ACTIONS = (
    "NOTION_SEARCH_NOTION_PAGE",
    "NOTION_FETCH_DATA",
    "NOTION_CREATE_NOTION_PAGE",
    "NOTION_ADD_PAGE_CONTENT",
)

PROMPT = (
    "Notion: search before creating so you do not duplicate pages. When creating a page, use the "
    "parent page the user names; if none is given and no default parent is configured, ask. "
    "Summarise search results as title — one line — link."
)

INTEGRATION = Integration(
    key="notion",
    label="Notion",
    app="notion",
    actions=ACTIONS,
    prompt=PROMPT,
    register_commands=commands.register,
    help_lines=(
        "`/notion search query:onboarding` → find pages",
        "`/notion note title:Standup 9/17 content:…` → create a page",
        "`@Dobby add today's decisions to the Roadmap page in Notion`",
    ),
)
