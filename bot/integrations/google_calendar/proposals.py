"""Adapt Composio's calendar schemas to the original, confirmation-gated meeting preview."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import logging
import re
from zoneinfo import ZoneInfo

import discord

from bot.AIModels import FunctionDeclaration

from ...composio import run_action
from ...memory import display_names_for_emails, find_user_by_discord_id
from ...voice import say
from ..base import PendingAction
from . import format as fmt

log = logging.getLogger("calendar_proposals")

DISCORD_ID = re.compile(r"[0-9]+")  # ASCII digits only: str.isdigit() also accepts ², ① and the like
RAW_MENTION = re.compile(r"<@!?[0-9]+>")

WRITE_ACTIONS = {
    "GOOGLECALENDAR_CREATE_EVENT": "create",
    "GOOGLECALENDAR_PATCH_EVENT": "update",
    "GOOGLECALENDAR_DELETE_EVENT": "delete",
}


def safe(value):
    return discord.utils.escape_mentions(discord.utils.escape_markdown(str(value)))


def event_from(data, event_id):
    if isinstance(data, dict):
        if data.get("id") == event_id and "start" in data:
            return data
        for value in data.values():
            found = event_from(value, event_id)
            if found is not None:
                return found
    elif isinstance(data, list):
        for value in data:
            found = event_from(value, event_id)
            if found is not None:
                return found
    return None


def event_over(event):
    """True when the event was cancelled or has already ended."""
    if event.get("status") == "cancelled":
        return True
    end = event.get("end", {}).get("dateTime")
    try:
        return bool(end) and datetime.fromisoformat(end).astimezone(timezone.utc) <= datetime.now(
            timezone.utc
        )
    except ValueError:
        return False


def with_deferred_invitees(declaration):
    """The create/edit schema plus the required list of people whose invitation must wait for an email."""
    schema = deepcopy(declaration.parameters or {"type": "object"})
    schema.setdefault("properties", {})["deferred_invitees"] = {
        "type": "array",
        "description": "People requested for THIS meeting whose calendar email is unknown. Dobby asks the "
        "requester to approve inviting each one once their email is saved; do not delay scheduling. "
        "Each needs the exact discord_id from an @mention or a lookup_calendar_email result. "
        "Use [] when nobody is waiting on an email for this meeting.",
        "items": {
            "type": "object",
            "properties": {"name": {"type": "string"}, "discord_id": {"type": "string"}},
            "required": ["name", "discord_id"],
        },
    }
    schema["required"] = [*schema.get("required", []), "deferred_invitees"]
    return FunctionDeclaration(name=declaration.name, description=declaration.description, parameters=schema)


def invalid_deferred(ctx, people):
    """Why a deferred_invitees list cannot be trusted, or None. Every entry needs an ID Dobby has seen."""
    if not isinstance(people, list):
        return "deferred_invitees must be a list."
    for person in people:
        if (
            not isinstance(person, dict)
            or not isinstance(person.get("name"), str)
            or not person["name"].strip()
        ):
            return "Each deferred invitee needs a name."
        discord_id = str(person.get("discord_id") or "")
        if not DISCORD_ID.fullmatch(discord_id):
            return (
                f"To invite {person['name'].strip()} once their email arrives, Dobby needs their exact "
                "Discord ID. Ask the requester to @mention them (or to give their email address), then try again."
            )
        if discord_id not in ctx.known_discord_ids:
            return f"Discord ID {discord_id} did not come from an @mention or a lookup. Use only IDs Dobby has seen."
    return None


async def known_names(ctx, addresses):
    """Registered display names for invitee addresses; previews fall back to bare addresses on failure."""
    try:
        return await display_names_for_emails(ctx.session, addresses)
    except Exception as exc:
        log.warning("invitee_names_unavailable type=%s", type(exc).__name__)
        return {}


async def waiting_label(ctx, person):
    """A deferred invitee as the requester should check them: registered name plus a live @mention."""
    try:
        user = await find_user_by_discord_id(ctx.session, person["discord_id"])
    except Exception as exc:
        log.warning("invitee_name_unavailable type=%s", type(exc).__name__)
        user = None
    shown = (user or {}).get("display_name") or person["name"]
    mention = f"<@{person['discord_id']}>"
    if RAW_MENTION.fullmatch(shown.strip()):  # the model had no name for them: show the mention once
        return mention
    return f"{safe(shown)} ({mention})"


async def prepare_calendar_action(ctx, name, arguments):
    operation = WRITE_ACTIONS[name]
    params = deepcopy(arguments)
    deferred = params.pop("deferred_invitees", None)
    if operation == "delete":
        deferred = []
    elif deferred is None:
        return {
            "success": False,
            "error": "deferred_invitees is required on create and edit calls. "
            "Pass [] when nobody is waiting on an email for this meeting.",
        }
    problem = invalid_deferred(ctx, deferred)
    if problem:
        return {"success": False, "error": problem}
    # One entry per Discord ID: the same person listed twice is still one invitation.
    deferred = list(
        {
            str(p["discord_id"]): {"name": p["name"].strip(), "discord_id": str(p["discord_id"])}
            for p in deferred
        }.values()
    )
    params.setdefault("calendar_id", "primary")
    params.setdefault("send_updates", "all")
    zone = params.get("timezone") or ctx.config.timezone
    old = {}

    async def fetch():
        return await run_action(
            ctx.toolset,
            "GOOGLECALENDAR_EVENTS_GET",
            {
                "calendar_id": params["calendar_id"],
                "event_id": params["event_id"],
            },
            ctx.config.composio_entity,
        )

    if operation != "create":
        if not params.get("event_id"):
            return {"success": False, "error": "Find the exact event before preparing this change."}
        result = await fetch()
        old = event_from(result.get("data"), params["event_id"]) if result.get("success") else None
        if not old:
            return {"success": False, "error": "Could not load the meeting; no change was queued."}
        if old.get("recurrence") or old.get("recurringEventId") or not old.get("start", {}).get("dateTime"):
            return {"success": False, "error": "Only single timed meetings can be changed."}
    if params.get("recurrence"):
        return {"success": False, "error": "Only single timed meetings are supported."}

    body = {
        key: deepcopy(params[key])
        for key in ("summary", "description", "location", "attendees")
        if key in params
    }
    if "attendees" in body:
        body["attendees"] = [{"email": a} if isinstance(a, str) else a for a in body["attendees"]]
    if operation != "delete":
        params["timezone"] = zone
        start_key = "start_datetime" if operation == "create" else "start_time"
        end_key = "end_datetime" if operation == "create" else "end_time"
        if operation == "create" and not params.get(start_key):
            return {"success": False, "error": "A meeting needs an exact start time."}
        for key, target in ((start_key, "start"), (end_key, "end")):
            if key in params:
                if len(params[key]) <= 10:
                    return {"success": False, "error": "Only single timed meetings are supported."}
                # Match Composio's wall-clock interpretation; make the offset explicit in the preview.
                dt = datetime.fromisoformat(params[key]).replace(tzinfo=ZoneInfo(zone))
                params[key] = dt.isoformat() if operation == "update" else dt.replace(tzinfo=None).isoformat()
                body[target] = {"dateTime": dt.isoformat()}
        if "start" in body and "end" not in body:
            if operation == "create":
                duration = timedelta(
                    hours=params.get("event_duration_hour", 0),
                    minutes=params.get("event_duration_minutes", 0),
                )
                if not duration:
                    duration = timedelta(hours=1)
                params["end_datetime"] = (datetime.fromisoformat(params[start_key]) + duration).isoformat()
            else:
                duration = datetime.fromisoformat(old["end"]["dateTime"]) - datetime.fromisoformat(
                    old["start"]["dateTime"]
                )
                params["end_time"] = (
                    datetime.fromisoformat(body["start"]["dateTime"]) + duration
                ).isoformat()
            body["end"] = {
                "dateTime": (datetime.fromisoformat(body["start"]["dateTime"]) + duration).isoformat()
            }
    merged = {**old, **body}
    if operation != "delete":
        start = datetime.fromisoformat(merged["start"]["dateTime"])
        end = datetime.fromisoformat(merged["end"]["dateTime"])
        if end <= start:
            return {"success": False, "error": "The meeting must end after it starts."}
    names = await known_names(ctx, {*fmt.emails(merged), *fmt.emails(old)})
    rows = [
        f"**Title:** {safe(merged.get('summary') or '(untitled)')}",
        "**When:** "
        + safe(
            fmt.when(merged.get("start", {}).get("dateTime"), merged.get("end", {}).get("dateTime"), zone)
        ),
    ]
    for key in ("location", "description"):
        if merged.get(key):
            rows.append(f"**{key.title()}:** {safe(merged[key])}")
    if merged.get("attendees"):
        rows.append("**Invitees:** " + safe(", ".join(fmt.invitee(e, names) for e in fmt.emails(merged))))
    if operation == "update":
        diff = fmt.changes(old, body, zone, names)
        if "attendees" in body:
            removed = sorted(set(fmt.emails(old)) - set(fmt.emails(body)))
            if removed:
                diff.append("Invitees removed: " + ", ".join(fmt.invitee(e, names) for e in removed))
        rows.append("**Changes:**\n" + "\n".join(f"• {safe(line)}" for line in diff or ["(nothing visible)"]))
    # Include every additional requested option, so the approved draft never hides a write parameter.
    shown = {
        "summary",
        "description",
        "location",
        "attendees",
        "start_datetime",
        "end_datetime",
        "start_time",
        "end_time",
        "event_duration_hour",
        "event_duration_minutes",
        "timezone",
        "event_id",
    }
    for key, value in params.items():
        if key not in shown:
            rows.append(f"**{key.replace('_', ' ').title()}:** {safe(json.dumps(value, ensure_ascii=False))}")
    if old.get("attendees") and params["send_updates"] == "all":
        rows.append(f"_Google will notify the {len(old['attendees'])} existing invitees._")
    if deferred:
        people = ", ".join([await waiting_label(ctx, p) for p in deferred])
        rows.append(
            f"**Waiting for email:** {people}\n"
            "Please reply with your email, or say `@Dobby my name is … and my email is …`. "
            "Dobby will schedule this meeting without waiting, then ask you to approve inviting them, "
            "with their address shown, once their email arrives."
        )

    async def execute():
        if old:
            latest = await fetch()
            current = event_from(latest.get("data"), params["event_id"]) if latest.get("success") else None
            if current != old:
                return {
                    "success": False,
                    "error": "The meeting changed since the preview. Ask for a new proposal.",
                }
        result = await run_action(ctx.toolset, name, deepcopy(params), ctx.config.composio_entity)
        if result.get("success"):
            result["message"] = say(
                "calendar_" + {"create": "created", "update": "updated", "delete": "deleted"}[operation]
            )
            result["message"] += "\n\n**Event:** " + safe(merged.get("summary") or "(untitled)")
            event_start = fmt.parse(merged.get("start", {}).get("dateTime"), zone)
            if event_start is not None:
                result["message"] += f", {event_start.month}/{event_start.day}"
            if operation == "update":
                result["message"] += "\n**Changed:**\n" + "\n".join(
                    f"• {safe(line)}" for line in diff or ["(no visible change)"]
                )
            if deferred:
                from .invitations import created_event_id, remember_invites

                event_id = params.get("event_id") or created_event_id(result)
                try:
                    if not event_id:
                        raise ValueError("Created event response contained no ID")
                    await remember_invites(ctx, params["calendar_id"], event_id, deferred)
                    result["deferred_invites"] = True
                    result["message"] += (
                        "\n\n**Waiting for email:** "
                        + people
                        + ". Please reply with your email or use `/email action:set`. "
                        "The meeting is scheduled; Dobby will ask you to approve inviting them once their email arrives."
                    )
                except Exception:
                    result["message"] += (
                        "\nThe meeting was saved, but Dobby could not remember the "
                        "missing invitations. Please ask Dobby to invite them again."
                    )
        return result

    queued = ctx.queue(
        PendingAction("google_calendar", f"{operation.title()} meeting", "\n".join(rows), execute)
    )
    queued["note"] = (
        "Shown as a meeting proposal. Ask the requester to react 🟢 to confirm or 🔴 to cancel. Nothing has changed."
    )
    return queued
