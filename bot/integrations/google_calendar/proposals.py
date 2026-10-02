"""Adapt Composio's calendar schemas to the original, confirmation-gated meeting preview."""

from copy import deepcopy
from datetime import datetime, timedelta
import json
from zoneinfo import ZoneInfo

import discord

from ...composio import run_action
from ...voice import say
from ..base import PendingAction
from . import format as fmt

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


async def prepare_calendar_action(ctx, name, arguments):
    operation = WRITE_ACTIONS[name]
    params = deepcopy(arguments)
    deferred = params.pop("deferred_invitees", list(ctx.missing_invitees.values()))
    if operation == "delete":
        deferred = []
    if not isinstance(deferred, list) or any(
        not isinstance(p, dict)
        or not isinstance(p.get("name"), str)
        or not p["name"].strip()
        or (p.get("discord_id") and not str(p["discord_id"]).isdigit())
        for p in deferred
    ):
        return {
            "success": False,
            "error": "Deferred invitees need a name and, when known, an exact Discord ID.",
        }
    deferred = deepcopy(deferred)
    for person in deferred:
        person["name"] = person["name"].strip()
        if person.get("discord_id"):
            person["discord_id"] = str(person["discord_id"])
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
        rows.append("**Invitees:** " + safe(", ".join(fmt.emails(merged))))
    if operation == "update":
        diff = fmt.changes(old, body, zone)
        if "attendees" in body:
            removed = sorted(set(fmt.emails(old)) - set(fmt.emails(body)))
            if removed:
                diff.append("Invitees removed: " + ", ".join(removed))
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
        people = ", ".join(safe(p["name"]) for p in deferred)
        rows.append(
            f"**Waiting for email:** {people}\n"
            "Please reply with your email, or say `@Dobby my name is … and my email is …`. "
            "Dobby will schedule this meeting without waiting. Confirming also approves inviting "
            "these people automatically when their addresses arrive."
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
                        "The meeting is scheduled; Dobby will add the missing invitations automatically."
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
    ctx.missing_invitees.clear()
    return queued
