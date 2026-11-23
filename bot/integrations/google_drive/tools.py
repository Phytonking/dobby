"""Drive tools that change who can see or keep a file. They queue a PendingAction; nothing runs until Confirm."""

import re

from bot.AIModels import FunctionDeclaration

from ...composio import find_key, run_action
from ..base import LocalTool, PendingAction, RunContext

# Composio action names — verify against the catalog at deploy time (docs/BOT_ARCHITECTURE.md).
GET_METADATA = "GOOGLEDRIVE_GET_FILE_METADATA"
TRASH_FILE = "GOOGLEDRIVE_TRASH_FILE"
SHARE_FILE = "GOOGLEDRIVE_ADD_FILE_SHARING_PREFERENCE"

ROLES = ("reader", "commenter", "writer")
EMAIL = re.compile(r"[^@\s<>]+@[^@\s<>]+\.[^@\s<>]+")


async def _file_name(ctx: RunContext, file_id: str) -> tuple[str | None, dict | None]:
    """The file's name for the preview, or an error result when it cannot be found."""
    meta = await run_action(ctx.toolset, GET_METADATA, {"file_id": file_id}, ctx.config.composio_entity)
    if not meta.get("success"):
        return None, meta
    name = find_key(meta.get("data"), "name", "title")
    if not name:
        return None, {"success": False, "error": "could not find that file"}
    return str(name), None


def draft_trash(toolset, entity_id: str, file_id: str, name: str) -> PendingAction:
    async def execute() -> dict:
        return await run_action(toolset, TRASH_FILE, {"file_id": file_id}, entity_id)

    return PendingAction(
        integration="google_drive",
        label="Drive trash",
        preview=f"Move to trash: {name}",
        execute=execute,
    )


def draft_share(toolset, entity_id: str, file_id: str, name: str, email: str, role: str) -> PendingAction:
    async def execute() -> dict:
        params = {"file_id": file_id, "type": "user", "role": role, "email_address": email}
        return await run_action(toolset, SHARE_FILE, params, entity_id)

    return PendingAction(
        integration="google_drive",
        label="Drive share",
        preview=f"Share {name} with {email} as {role}",
        execute=execute,
    )


async def trash_tool(ctx: RunContext, params: dict) -> dict:
    file_id = str(params.get("file_id", "")).strip()
    if not file_id:
        return {"success": False, "error": "file_id is required"}
    name, error = await _file_name(ctx, file_id)
    if error:
        return error
    return ctx.queue(draft_trash(ctx.toolset, ctx.config.composio_entity, file_id, name))


async def share_tool(ctx: RunContext, params: dict) -> dict:
    file_id = str(params.get("file_id", "")).strip()
    email = str(params.get("email", "")).strip()
    role = params.get("role") or "reader"
    if not file_id:
        return {"success": False, "error": "file_id is required"}
    if not EMAIL.fullmatch(email):
        return {"success": False, "error": "a valid email address is required"}
    if role not in ROLES:
        return {"success": False, "error": f"role must be one of {', '.join(ROLES)}"}
    name, error = await _file_name(ctx, file_id)
    if error:
        return error
    return ctx.queue(draft_share(ctx.toolset, ctx.config.composio_entity, file_id, name, email, role))


TRASH_TOOL = LocalTool(
    declaration=FunctionDeclaration(
        name="trash_drive_file",
        description=(
            "Queue moving a Drive file to the trash for the requester to confirm in Discord. Nothing is "
            "trashed until they press Confirm. Find the file ID with GOOGLEDRIVE_FIND_FILE first."
        ),
        parameters={
            "type": "object",
            "properties": {"file_id": {"type": "string", "description": "Drive file ID"}},
            "required": ["file_id"],
        },
    ),
    handler=trash_tool,
)

SHARE_TOOL = LocalTool(
    declaration=FunctionDeclaration(
        name="share_drive_file",
        description=(
            "Queue sharing a Drive file with one person for the requester to confirm in Discord. Nothing "
            "is shared until they press Confirm. Only use an email address the user supplied."
        ),
        parameters={
            "type": "object",
            "properties": {
                "file_id": {"type": "string", "description": "Drive file ID"},
                "email": {"type": "string", "description": "Recipient's email address"},
                "role": {"type": "string", "enum": list(ROLES), "description": "Defaults to reader"},
            },
            "required": ["file_id", "email"],
        },
    ),
    handler=share_tool,
)

TOOLS = (TRASH_TOOL, SHARE_TOOL)
