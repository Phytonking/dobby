"""Luma through its MCP server: whatever tools Luma offers, read ones immediately, changes after Confirm.

The model sees two functions rather than a hard-coded copy of Luma's API:
- ``luma_tools`` lists what Luma's MCP server currently offers (name, purpose, arguments, and
  whether it only reads);
- ``luma_call`` runs one. Tools Luma marks read-only (``readOnlyHint``) run straight away. Every
  other tool — creating events, inviting or approving guests, email blasts — is queued as a
  PendingAction with the exact arguments as its preview and runs only when the requester confirms.
  A tool Luma doesn't annotate is treated as a change.
"""

import json

from bot.AIModels import FunctionDeclaration

from ...models import UserError
from ..base import LocalTool, PendingAction, RunContext
from . import mcp

MAX_PREVIEW = 1500


def _client(ctx: RunContext) -> mcp.LumaClient:
    """Tests (and nothing else) put a client on the config."""
    return getattr(ctx.config, "luma_client", None) or mcp.CLIENT


async def list_tools(ctx: RunContext, params: dict) -> dict:
    try:
        catalog = await _client(ctx).tools(refresh=bool(params.get("refresh")))
    except UserError as exc:
        return {"success": False, "error": str(exc)}
    return {
        "success": True,
        "tools": [
            {
                "name": t.name,
                "title": t.title,
                "description": t.description,
                "read_only": t.read_only,
                "arguments": t.input_schema,
            }
            for t in catalog
        ],
        "note": "Call luma_call with one of these names. read_only tools run at once; the rest are "
        "shown to the requester for Confirm before anything changes in Luma.",
    }


def preview(tool: mcp.LumaTool, arguments: dict) -> str:
    shown = json.dumps(arguments, indent=2, ensure_ascii=False, default=str)
    if len(shown) > MAX_PREVIEW:
        shown = shown[:MAX_PREVIEW] + "\n…"
    return f"Luma: {tool.title}\n```json\n{shown}\n```"


def draft_change(client: mcp.LumaClient, tool: mcp.LumaTool, arguments: dict) -> PendingAction:
    frozen = json.loads(json.dumps(arguments))  # what runs is exactly what was previewed

    async def execute() -> dict:
        try:
            result = await client.call(tool.name, frozen)
        except UserError as exc:
            return {"success": False, "error": str(exc)}
        if result.get("isError"):
            return {"success": False, "error": mcp.result_text(result, 300) or "Luma refused the change"}
        return {"success": True, "result": mcp.result_text(result, 1500)}

    return PendingAction(
        integration="luma", label=f"Luma: {tool.title}", preview=preview(tool, frozen), execute=execute
    )


async def call_tool(ctx: RunContext, params: dict) -> dict:
    name = str(params.get("tool") or "").strip()
    arguments = params.get("arguments") or {}
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except ValueError:
            return {"success": False, "error": "arguments must be a JSON object"}
    if not isinstance(arguments, dict):
        return {"success": False, "error": "arguments must be a JSON object"}
    client = _client(ctx)
    try:
        catalog = {t.name: t for t in await client.tools()}
        if name not in catalog:  # Luma may have added it since the last listing
            catalog = {t.name: t for t in await client.tools(refresh=True)}
    except UserError as exc:
        return {"success": False, "error": str(exc)}
    tool = catalog.get(name)
    if tool is None:
        return {"success": False, "error": f"Luma has no tool called {name!r}; call luma_tools to see them"}

    if not tool.read_only:
        return ctx.queue(draft_change(client, tool, arguments))

    try:
        result = await client.call(tool.name, arguments)
    except UserError as exc:
        return {"success": False, "error": str(exc)}
    if result.get("isError"):
        return {"success": False, "error": mcp.result_text(result, 500) or "Luma returned an error"}
    return {"success": True, "result": mcp.result_text(result)}


LIST_TOOLS = LocalTool(
    FunctionDeclaration(
        name="luma_tools",
        description=(
            "List what Dobby can do in Luma (events, guests, invites, calendars) through Luma's MCP "
            "server: each tool's name, purpose, arguments and whether it only reads. Call this before "
            "luma_call."
        ),
        parameters={
            "type": "object",
            "properties": {
                "refresh": {"type": "boolean", "description": "Re-ask Luma instead of using the cached list"}
            },
        },
    ),
    list_tools,
)

CALL_TOOL = LocalTool(
    FunctionDeclaration(
        name="luma_call",
        description=(
            "Run one Luma tool from luma_tools. Read-only tools return their result; anything that "
            "changes Luma (create/update events, invite/approve guests, blasts) is shown to the "
            "requester with Confirm/Cancel and only happens if they confirm."
        ),
        parameters={
            "type": "object",
            "properties": {
                "tool": {"type": "string", "description": "Exact tool name from luma_tools"},
                "arguments": {
                    "type": "string",
                    "description": 'JSON object of arguments matching that tool\'s schema, e.g. {"event_id": "evt-123"}',
                },
            },
            "required": ["tool"],
        },
    ),
    call_tool,
)

TOOLS = (LIST_TOOLS, CALL_TOOL)
