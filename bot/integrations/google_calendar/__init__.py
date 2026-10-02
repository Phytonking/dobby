"""Google Calendar: the team calendar Dobby creates, moves and deletes events on."""

from ..base import Integration
from . import commands
from .tools import LOOKUP_TOOL

# Curated schemas: the registry wraps writes with local confirmation handlers.
# Verified against Composio's catalog at deploy time
# (see docs/BOT_ARCHITECTURE.md → "Adding an integration").
ACTIONS = (
    "GOOGLECALENDAR_CREATE_EVENT",
    "GOOGLECALENDAR_FIND_EVENT",
    "GOOGLECALENDAR_PATCH_EVENT",
    "GOOGLECALENDAR_DELETE_EVENT",
    "GOOGLECALENDAR_FIND_FREE_SLOTS",
)

PROMPT = (
    "Google Calendar: events go on the team calendar. To invite someone, call "
    "lookup_calendar_email with their name; if it returns no email, say that person has no "
    "calendar email on file and ask them to reply with it. Never guess an address. "
    "Do NOT wait for missing emails: prepare the meeting with known attendees immediately. "
    "Include missing people in deferred_invitees on the create/patch call, using their name and "
    "exact discord_id when mentioned. Only include people actually requested for that event. "
    "Dobby will invite them automatically when their email is saved, after the proposal is confirmed. "
    "Default meeting length is one hour. "
    "Calendar writes only prepare proposals; nothing changes until the requester reacts in Discord. "
    "Find the exact event before editing or deleting; ask which event if matches are ambiguous. "
    "Use PATCH_EVENT for edits, supplying only the fields the requester wants changed. "
    "Attendees replaces the guest list: retain existing guests when adding invitees. "
    "Only single timed meetings are supported. Never claim a queued proposal has been applied."
)

INTEGRATION = Integration(
    key="google_calendar",
    label="Google Calendar",
    app="googlecalendar",
    actions=ACTIONS,
    local_tools=(LOOKUP_TOOL,),
    prompt=PROMPT,
    register_commands=commands.register,
    help_lines=(
        "`/schedule request:Create Project Sync on October 12, 2026 at 10am for 30 minutes`",
        "`/schedule request:Move the design review to October 13 at 2pm`",
        "`/events days:30` → upcoming events",
        "`@Dobby set up a design review Friday at 2pm and invite Maya and @Leonard`",
    ),
)
