from dataclasses import dataclass
from hashlib import sha256
import re
from .models import UserError, event_body


@dataclass(frozen=True)
class Proposal:
    action: str
    body: dict
    event_id: str | None
    etag: str | None
    operation_id: str
    existing: dict | None


class Scheduler:
    def __init__(self, planner, calendar, timezone):
        self.planner, self.calendar, self.timezone = planner, calendar, timezone

    def prepare(self, request, event_id, interaction_id, history=None, place=None):
        if event_id and not re.fullmatch(r"[a-zA-Z0-9_-]{1,1024}", event_id):
            raise UserError("Copy an event ID from /events.")
        existing = self.calendar.get(event_id) if event_id else None
        if existing and (
            existing.get("recurrence")
            or existing.get("recurringEventId")
            or "date" in existing.get("start", {})
            or existing.get("eventType", "default") != "default"
            or existing.get("status") == "cancelled"
        ):
            raise UserError("Only active, single, timed meetings can be changed in this version.")
        plan = self.planner.plan(request, existing, history, place)
        if plan.action == "clarify":
            raise UserError(plan.question or "Please include a title, date, time and duration.")
        if plan.action in ("update", "delete") and not existing:
            raise UserError("Select the exact event using the event_id option from /events.")
        if plan.action == "create" and existing:
            raise UserError("To create a meeting, leave event_id empty.")
        if existing and not existing.get("etag"):
            raise UserError("Event has no version information. Try refreshing /events.")
        body = {} if plan.action == "delete" else event_body(plan, existing, self.timezone)
        if plan.action != "delete":
            self.calendar.check_conflicts(body, event_id)
        return Proposal(
            plan.action,
            body,
            event_id,
            (existing or {}).get("etag"),
            sha256(str(interaction_id).encode()).hexdigest(),
            existing,
        )
