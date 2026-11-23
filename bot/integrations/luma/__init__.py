"""Luma (luma.com) through Luma's official MCP server: reads run directly; every change waits for Confirm."""

from ..base import Integration
from . import commands
from .tools import TOOLS

PROMPT = (
    "Luma (events, guest lists, invites, calendars) is reached through Luma's MCP server: call "
    "luma_tools to see what it offers, then luma_call with a tool name and JSON arguments. Find IDs "
    "with read tools before using them. Changes (creating or editing events, inviting, approving or "
    "declining guests, blasts) are shown to the requester for Confirm — say so instead of claiming "
    "they're done. Only invite email addresses the user gave you. If Luma isn't connected, say an "
    "admin can connect it on the dashboard."
)

INTEGRATION = Integration(
    key="luma",
    label="Luma",
    app="luma",  # not a Composio toolkit: Luma's MCP server, OAuth connected on the dashboard
    local_tools=TOOLS,
    prompt=PROMPT,
    register_commands=commands.register,
    help_lines=(
        "`/luma events` → upcoming Luma events",
        "`/luma guests event:Demo Night` → who is registered",
        "`@Dobby invite sam@example.com to Demo Night on Luma` → preview, then Confirm",
    ),
)
