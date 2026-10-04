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
    "lookup_calendar_email with their name, adding discord_id when they were @mentioned. If it returns "
    "an email, add it to attendees. Never guess an address. "
    "If it returns found=false because the person has no calendar email yet, do NOT wait: prepare the "
    "meeting now with the known attendees and list that person in deferred_invitees with their name and "
    "exact discord_id (from the @mention or the lookup result). Dobby asks the requester to approve "
    "inviting them, with the address shown, once their email is saved. "
    "If the lookup is ambiguous or finds nobody, do not prepare the meeting yet: ask the requester which "
    "person they mean, to @mention them, or for the address, and wait for the answer. Never choose for them. "
    "Every create and edit call must include deferred_invitees: [] when nobody is waiting on an email "
    "for that meeting, and only people actually requested for that particular meeting otherwise. "
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
