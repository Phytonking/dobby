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


def event_body(plan, existing, zone, emails=()):
    if plan.action == "create" and not (plan.summary and plan.summary.strip()):
        raise UserError("Please supply a meeting title.")
    body = {
        k: getattr(plan, k) for k in ("summary", "description", "location") if getattr(plan, k) is not None
    }
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
        raise UserError("No changes were requested.")
    return body
