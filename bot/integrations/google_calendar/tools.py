"""Local tools: things the calendar needs that never go through Composio."""

from bot.AIModels import FunctionDeclaration

from ...memory import find_user_by_discord_id, match_user_by_name
from ..base import LocalTool, RunContext

WAITING_NOTE = (
    "No calendar email on file for this person. Prepare the event now without them and list them in "
    "deferred_invitees with this exact discord_id: Dobby asks the requester to approve inviting them once "
    "their email is saved. Tell the requester the person needs to add an email."
)


def found(user: dict) -> dict:
    return {
        "success": True,
        "found": True,
        "display_name": user["display_name"],
        "email": user["calendar_email"],
    }


def waiting(ctx: RunContext, display_name: str, discord_id: str) -> dict:
    ctx.known_discord_ids.add(discord_id)
    return {
        "success": True,
        "found": False,
        "display_name": display_name,
        "discord_id": discord_id,
        "note": WAITING_NOTE,
    }


async def lookup_calendar_email(ctx: RunContext, params: dict) -> dict:
    name = str(params.get("name") or "").strip()
    discord_id = str(params.get("discord_id") or "").strip()
    if discord_id:
        if discord_id not in ctx.known_discord_ids:
            return {
                "success": False,
                "error": "Unknown discord_id. Use only the exact ID of someone @mentioned in the request.",
            }
        user = await find_user_by_discord_id(ctx.session, discord_id)
        if user and user.get("calendar_email"):
            return found(user)
        return waiting(ctx, (user or {}).get("display_name") or name or discord_id, discord_id)
    if not name:
        return {"success": False, "error": "Give the person's name, or their discord_id if @mentioned."}

    match = await match_user_by_name(ctx.session, name)
    if match.user and match.user.get("calendar_email"):
        return found(match.user)
    if match.user:
        if match.user.get("discord_id"):
            return waiting(ctx, match.user["display_name"], str(match.user["discord_id"]))
        return {
            "success": True,
            "found": False,
            "display_name": match.user["display_name"],
            "note": "No calendar email and no Discord account on file. Ask the requester for the person's "
            "email address; never guess one.",
        }
    if match.ambiguous:
        return {
            "success": True,
            "found": False,
            "ambiguous": True,
            "candidates": list(match.candidates),
            "note": "More than one person fits that name. Do not prepare the meeting yet: ask the requester "
            "which one they mean (full name or @mention), then look up again. Do not choose for them.",
        }
    result = {
        "success": True,
        "found": False,
        "note": "Nobody registered goes by that name. Do not prepare the meeting yet: ask the requester to "
        "@mention the person or give their calendar email. Never guess an address.",
    }
    if match.candidates:
        result["suggestions"] = list(match.candidates)
        result["note"] = (
            "Nobody registered goes by exactly that name. Do not prepare the meeting yet: ask the requester "
            "whether they mean one of the suggestions, to @mention the person, or for their calendar email. "
            "Never guess an address."
        )
    return result


LOOKUP_TOOL = LocalTool(
    declaration=FunctionDeclaration(
        name="lookup_calendar_email",
        description=(
            "Find a registered server member's calendar email by name so they can be invited to an event. "
            "Returns the matched display name and email, or found=false with what to do next "
            "(no email yet, several people fit, or nobody by that name)."
        ),
        parameters={
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "The person's name as written"},
                "discord_id": {
                    "type": "string",
                    "description": "Exact Discord ID if the person was @mentioned in the request",
                },
            },
            "required": ["name"],
        },
    ),
    handler=lookup_calendar_email,
)
