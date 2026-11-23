"""Luma (lu.ma): events and guest lists, read directly; invites behind Confirm."""

from ..base import Integration
from . import commands
from .tools import TOOLS

PROMPT = (
    "Luma: use list_luma_events to find an event ID before get_luma_event or list_luma_guests. "
    "To invite people, call draft_luma_invites; it is shown to the requester for confirmation and "
    "you cannot send invites directly. Only invite email addresses the user supplied."
)

INTEGRATION = Integration(
    key="luma",
    label="Luma",
    app="luma",  # not a Composio toolkit: calls Luma's public API with LUMA_API_KEY
    local_tools=TOOLS,
    prompt=PROMPT,
    register_commands=commands.register,
    help_lines=(
        "`/luma events` → upcoming Luma events",
        "`/luma guests event:Demo Night` → who is registered",
        "`@Dobby invite sam@example.com to Demo Night on Luma` → preview, then Confirm",
    ),
)
