"""Luma tools: reads run immediately; inviting guests emails real people, so it waits for Confirm."""

from bot.AIModels import FunctionDeclaration

from ..base import LocalTool, PendingAction, RunContext
from .api import call

MAX_INVITEES = 25
MAX_MESSAGE = 200
STATUSES = ("approved", "session", "pending_approval", "invited", "declined", "waitlist")


def _key(ctx: RunContext) -> str:
    return getattr(ctx.config, "luma_api_key", "")


def _slim_event(entry: dict) -> dict:
    event = entry.get("event", entry)
    keep = ("id", "name", "start_at", "end_at", "timezone", "url", "max_capacity", "spots_remaining")
    return {k: event[k] for k in keep if k in event}


async def list_events(ctx: RunContext, params: dict) -> dict:
    query = {k: params[k] for k in ("after", "before") if params.get(k)}
    query["pagination_limit"] = min(int(params.get("limit") or 10), 50)
    result = await call(_key(ctx), "GET", "/v1/calendars/events/list", params=query)
    if not result["success"]:
        return result
    data = result["data"]
    return {
        "success": True,
        "events": [_slim_event(e) for e in data.get("entries", [])],
        "has_more": data.get("has_more", False),
    }


async def get_event(ctx: RunContext, params: dict) -> dict:
    event_id = str(params.get("event_id", "")).strip()
    if not event_id:
        return {"success": False, "error": "event_id is required"}
    return await call(_key(ctx), "GET", "/v1/events/get", params={"event_id": event_id})


async def list_guests(ctx: RunContext, params: dict) -> dict:
    event_id = str(params.get("event_id", "")).strip()
    if not event_id:
        return {"success": False, "error": "event_id is required"}
    query = {"event_id": event_id, "pagination_limit": min(int(params.get("limit") or 25), 100)}
    if params.get("approval_status") in STATUSES:
        query["approval_status"] = params["approval_status"]
    result = await call(_key(ctx), "GET", "/v1/events/guests/list", params=query)
    if not result["success"]:
        return result
    data = result["data"]
    keep = ("user_name", "user_email", "approval_status", "registered_at")
    guests = [{k: g.get(k) for k in keep} for g in data.get("entries", [])]
    return {"success": True, "guests": guests, "has_more": data.get("has_more", False)}


def draft_invites(api_key: str, event_id: str, emails: list[str], message: str | None) -> PendingAction:
    body = {"event_id": event_id, "guests": [{"email": e} for e in emails]}
    if message:
        body["message"] = message
    preview = f"Luma invites for event {event_id} to: {', '.join(emails)}" + (
        f"\nMessage: {message}" if message else ""
    )

    async def execute() -> dict:
        result = await call(api_key, "POST", "/v1/events/guests/send-invites", body=body)
        if result["success"]:
            return {"success": True, "skipped": result["data"].get("skipped", [])}
        return result

    return PendingAction(integration="luma", label="Luma invites", preview=preview, execute=execute)


async def draft_invites_tool(ctx: RunContext, params: dict) -> dict:
    event_id = str(params.get("event_id", "")).strip()
    emails = [str(e).strip() for e in params.get("emails") or [] if str(e).strip()]
    message = str(params.get("message") or "").strip() or None
    if not event_id or not emails:
        return {"success": False, "error": "event_id and emails are required"}
    if len(emails) > MAX_INVITEES:
        return {"success": False, "error": f"at most {MAX_INVITEES} invitees per request"}
    if message and len(message) > MAX_MESSAGE:
        return {
            "success": False,
            "error": f"message is {len(message)} characters; the limit is {MAX_MESSAGE}",
        }
    return ctx.queue(draft_invites(_key(ctx), event_id, emails, message))


def _tool(name: str, description: str, properties: dict, required: list[str], handler) -> LocalTool:
    schema = {"type": "object", "properties": properties}
    if required:
        schema["required"] = required
    return LocalTool(FunctionDeclaration(name=name, description=description, parameters=schema), handler)


_str = {"type": "string"}
LIST_EVENTS = _tool(
    "list_luma_events",
    "List the group's Luma events, soonest first. Optional ISO 8601 `after`/`before` bounds.",
    {"after": _str, "before": _str, "limit": {"type": "integer"}},
    [],
    list_events,
)
GET_EVENT = _tool(
    "get_luma_event",
    "Details and guest counts for one Luma event (id starts with evt-).",
    {"event_id": _str},
    ["event_id"],
    get_event,
)
LIST_GUESTS = _tool(
    "list_luma_guests",
    "Guests for a Luma event, optionally filtered by approval_status.",
    {
        "event_id": _str,
        "approval_status": {"type": "string", "enum": list(STATUSES)},
        "limit": {"type": "integer"},
    },
    ["event_id"],
    list_guests,
)
DRAFT_INVITES = _tool(
    "draft_luma_invites",
    "Queue Luma invite emails for the requester to confirm in Discord. Nothing is sent until they press Confirm.",
    {"event_id": _str, "emails": {"type": "array", "items": _str}, "message": _str},
    ["event_id", "emails"],
    draft_invites_tool,
)
TOOLS = (LIST_EVENTS, GET_EVENT, LIST_GUESTS, DRAFT_INVITES)
