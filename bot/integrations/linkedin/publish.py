"""Build LinkedIn posts as PendingActions; nothing here runs until the requester confirms."""

from bot.AIModels import FunctionDeclaration

from ...composio import find_key, run_action
from ..base import LocalTool, PendingAction, RunContext

# Composio action names — verify against the catalog at deploy time (docs/BOT_ARCHITECTURE.md).
GET_ME = "LINKEDIN_GET_MY_INFO"
CREATE_POST = "LINKEDIN_CREATE_LINKED_IN_POST"
MAX_CHARS = 1300


def author_urn(me: dict) -> str | None:
    """The member URN Composio returns for the connected account, in whichever field it uses."""
    urn = find_key(me, "author", "author_id", "author_urn", "urn")
    if isinstance(urn, str) and urn.startswith("urn:li:"):
        return urn
    member_id = find_key(me, "sub", "id")
    return f"urn:li:person:{member_id}" if member_id else None


def draft_post(toolset, entity_id: str, text: str) -> PendingAction:
    text = text.strip()

    async def execute() -> dict:
        me = await run_action(toolset, GET_ME, {}, entity_id)
        if not me.get("success"):
            return me
        author = author_urn(me.get("data"))
        if not author:
            return {"success": False, "error": "could not determine the LinkedIn author URN"}
        return await run_action(
            toolset,
            CREATE_POST,
            {"author": author, "commentary": text, "visibility": "PUBLIC", "lifecycle_state": "PUBLISHED"},
            entity_id,
        )

    return PendingAction(integration="linkedin", label="LinkedIn post", preview=text, execute=execute)


async def draft_tool(ctx: RunContext, params: dict) -> dict:
    text = str(params.get("text", "")).strip()
    if not text:
        return {"success": False, "error": "text is required"}
    if len(text) > MAX_CHARS:
        return {"success": False, "error": f"post is {len(text)} characters; the limit is {MAX_CHARS}"}
    return ctx.queue(draft_post(ctx.toolset, ctx.config.composio_entity, text))


DRAFT_TOOL = LocalTool(
    declaration=FunctionDeclaration(
        name="draft_linkedin_post",
        description=(
            "Queue a LinkedIn post for the requester to confirm in Discord. Nothing is published "
            "until they press Confirm. Pass the complete, final post text."
        ),
        parameters={
            "type": "object",
            "properties": {"text": {"type": "string", "description": "The full post text"}},
            "required": ["text"],
        },
    ),
    handler=draft_tool,
)
