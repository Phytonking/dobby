from datetime import datetime, timedelta, timezone
from typing import Literal
from zoneinfo import ZoneInfo
from pydantic import BaseModel, ConfigDict, Field


class UserError(Exception):
    pass


class ConfigError(Exception):
    """Startup misconfiguration. Messages are authored here, so they are safe to display."""


class Plan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["create", "update", "delete", "clarify"]
    question: str | None = None
    summary: str | None = Field(default=None, max_length=200)
    start: str | None = None
    end: str | None = None
    description: str | None = Field(default=None, max_length=1000)
    location: str | None = Field(default=None, max_length=200)
    # People to invite, exactly as the user wrote them (names or email addresses).
    invitees: list[str] = Field(default_factory=list, max_length=20)
    # For update/delete without a selected event: the meeting the user means.
    target_title: str | None = Field(default=None, max_length=200)
    # One- or two-word label for a multi-word thread/channel name, used as the title prefix.
    place_label: str | None = Field(default=None, max_length=40)


def is_editable(event):
    """Only active, single, timed, default events can be changed or matched by title."""
    return not (
        event.get("recurrence")
        or event.get("recurringEventId")
        or "date" in event.get("start", {})
        or event.get("eventType", "default") != "default"
        or event.get("status") == "cancelled"
    )


def interval(start, end):
    try:
        a, b = datetime.fromisoformat(start), datetime.fromisoformat(end)
        if a.utcoffset() is None or b.utcoffset() is None:
            raise ValueError()
    except ValueError, TypeError:
        raise UserError("Use exact dates and times with a timezone.") from None
    if b <= a or b - a > timedelta(hours=24):
        raise UserError("Meeting duration must be positive and at most 24 hours.")
    if a < datetime.now(timezone.utc):
        raise UserError("Meetings must start in the future.")
    return a, b


TITLE_SEPARATOR = " | "


MAX_LABEL_WORDS = 2


def concise(text, limit=MAX_LABEL_WORDS):
    """At most `limit` words, punctuation trimmed; '' when nothing usable is left."""
    words = [w.strip("|/\\-–—:;,.!?()[]{}\"'`#*_~") for w in str(text or "").split()]
    words = [w for w in words if w]
    return " ".join(words[:limit])


def place_name(place, label=None):
    """Prefix for titles: the thread name (or channel name) when it is a single word,
    otherwise Gemini's short label for it, otherwise its first words."""
    place = place or {}
    raw = " ".join(str(place.get("thread") or place.get("channel") or "").split())
    if len(raw.split()) <= 1:
        return raw
    short = concise(label)
    if short and len(short) <= 30 and short.lower() != raw.lower():
        return short
    return concise(raw)


def titled(prefix, summary):
    """Every event title reads "<thread or channel> | <event name>"; never prefix twice.

    A thread named after the meeting itself ("Design review" in thread "Design review")
    stays a single "Design review", and an already doubled "X | X" collapses back to "X".
    """
    summary = " ".join(str(summary or "").split())
    parts = summary.split(TITLE_SEPARATOR)
    if len(parts) == 2 and parts[0].strip().lower() == parts[1].strip().lower():
        summary = parts[0].strip()
    if not prefix or not summary or TITLE_SEPARATOR in summary:
        return summary
    if summary.lower() == prefix.lower():
        return summary
    return f"{prefix}{TITLE_SEPARATOR}{summary}"[:200]


def event_label(event, zone):
    """Confirmation text: "<thread or channel> | <event name>, <month>/<day>" in the team zone."""
    summary = " ".join(str(event.get("summary") or "(untitled)").split())
    start = event.get("start", {}).get("dateTime") or event.get("start", {}).get("date")
    try:
        when = datetime.fromisoformat(start)
        if when.tzinfo is not None:
            when = when.astimezone(ZoneInfo(zone))
        return f"{summary}, {when.month}/{when.day}"
    except TypeError, ValueError:
        return summary


def event_body(plan, existing, zone, emails=(), place=None):
    if plan.action == "create" and not (plan.summary and plan.summary.strip()):
        raise UserError("Please supply a meeting title.")
    body = {
        k: getattr(plan, k) for k in ("summary", "description", "location") if getattr(plan, k) is not None
    }
    if "summary" in body:
        body["summary"] = titled(place_name(place, plan.place_label), body["summary"])
    if plan.action == "update" and existing:
        # Gemini often echoes fields it did not change; only real differences are sent.
        for key in ("summary", "description", "location"):
            if key in body and body[key] == existing.get(key):
                del body[key]
    if plan.action == "create" or plan.start is not None or plan.end is not None:
        start = plan.start or (existing or {}).get("start", {}).get("dateTime")
        end = plan.end or (existing or {}).get("end", {}).get("dateTime")
        if plan.action == "create" and start and not end:
            try:
                end = (datetime.fromisoformat(start) + timedelta(hours=1)).isoformat()
            except ValueError:
                raise UserError("Use an exact start date and time with a timezone.") from None
        if plan.action == "update" and plan.start and not plan.end and existing:
            try:
                duration = datetime.fromisoformat(existing["end"]["dateTime"]) - datetime.fromisoformat(
                    existing["start"]["dateTime"]
                )
                end = (datetime.fromisoformat(start) + duration).isoformat()
            except KeyError, ValueError, TypeError:
                raise UserError("Could not determine the original meeting duration.") from None
        a, b = interval(start, end)
        if plan.action == "update" and existing and same_span(existing, a, b):
            pass  # asked to move a meeting to where it already is
        else:
            body.update(
                start={"dateTime": a.astimezone(ZoneInfo(zone)).isoformat(), "timeZone": zone},
                end={"dateTime": b.astimezone(ZoneInfo(zone)).isoformat(), "timeZone": zone},
            )
    if emails:
        # PATCH replaces the whole attendee array, so merge with existing guests.
        current = [a for a in (existing or {}).get("attendees", []) if a.get("email")]
        known = {a["email"].lower() for a in current}
        added = [{"email": e} for e in dict.fromkeys(e.lower() for e in emails) if e not in known]
        if added or plan.action == "create":
            body["attendees"] = current + added
    if not body:
        raise UserError(
            "Nothing would change: the meeting already has those details. "
            "Say what should be different, for example the new time or title."
        )
    return body


def same_span(existing, start, end):
    try:
        old_start = datetime.fromisoformat(existing["start"]["dateTime"])
        old_end = datetime.fromisoformat(existing["end"]["dateTime"])
    except KeyError, TypeError, ValueError:
        return False
    return old_start == start and old_end == end
