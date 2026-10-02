"""Local tools: things the calendar needs that never go through Composio."""

from bot.AIModels import FunctionDeclaration

from ...memory import find_user_by_name, find_user_by_discord_id
from ..base import LocalTool, RunContext


async def lookup_calendar_email(ctx: RunContext, params: dict) -> dict:
    name = params.get("name", "")
    discord_id = params.get("discord_id")
    user = (
        await find_user_by_discord_id(ctx.session, discord_id)
        if discord_id
        else await find_user_by_name(ctx.session, name)
    )
    if user is None or not user.get("calendar_email"):
        ctx.missing_invitees[name.casefold()] = {"name": name, "discord_id": discord_id}
        return {
            "success": True,
            "found": False,
            "name": name,
            "note": "Ask this person for their email, but prepare the event now without them. "
            "Include them in deferred_invitees; confirmation authorizes inviting them later.",
        }
    return {
        "success": True,
        "found": True,
        "display_name": user["display_name"],
        "email": user["calendar_email"],
    }


LOOKUP_TOOL = LocalTool(
    declaration=FunctionDeclaration(
        name="lookup_calendar_email",
        description=(
            "Find a server member's calendar email by their name so they can be invited to an event. "
            "Returns the matched display name and email, or found=false."
        ),
        parameters={
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "The person's name as written"},
                "discord_id": {
                    "type": "string",
                    "description": "Exact Discord ID if the person was mentioned",
                },
            },
            "required": ["name"],
        },
    ),
    handler=lookup_calendar_email,
)
