from dataclasses import dataclass, field
from hashlib import sha256
import re
from .lookup import clear_winner, find_events
from .models import UserError, event_body, is_editable
from .voice import say


@dataclass(frozen=True)
class Proposal:
    action: str
    body: dict
    event_id: str | None
    etag: str | None
    operation_id: str
    existing: dict | None
    attendees: tuple = field(default=())
    resolved_by_title: bool = False


class NeedContacts(UserError):
    """Invitees whose email Dobby does not know yet. The bot asks, saves, then retries."""

    def __init__(self, names):
        self.names = list(names)
        super().__init__(
            "Dobby does not know an email address for " + ", ".join(self.names) + ". "
            "Reply to Dobby's question with `Name: name@example.com`, or use /contacts add."
        )


class NeedTitle(UserError):
    """Update/delete named no meeting. The bot asks for a title, then retries with it."""

    def __init__(self):
        super().__init__(
            "Which meeting? Reply with its title, or select the exact event using the "
            "event_id option from /events."
        )


class ChooseEvent(UserError):
    """Several upcoming events resemble the title. The bot lists them; a reply picks one."""

    def __init__(self, title, candidates):
        self.title, self.candidates = title, list(candidates)
        rows = "; ".join(
            f"{i}. {c.get('summary', '(untitled)')} at {c.get('start', {}).get('dateTime', '')} "
            f"(event_id:{c['id']})"
            for i, c in enumerate(self.candidates, 1)
        )
        super().__init__(
            f"Several meetings resemble '{title}': {rows}. Repeat the request with one event_id."
        )


class Scheduler:
    def __init__(self, planner, calendar, timezone, contacts=None):
        self.planner, self.calendar, self.timezone = planner, calendar, timezone
        self.contacts = contacts

    def resolve_invitees(self, names):
        if not names:
            return {}, []
        if self.contacts is None:
            emails = {n: n for n in names if "@" in n}
            return emails, [n for n in names if n not in emails]
        return self.contacts.lookup(names)

    def prepare(self, request, event_id, interaction_id, history=None, place=None, title=None):
        if event_id and not re.fullmatch(r"[a-zA-Z0-9_-]{1,1024}", event_id):
            raise UserError("Copy an event ID from /events.")
        existing = self.calendar.get(event_id) if event_id else None
        if existing and not is_editable(existing):
            raise UserError("Only active, single, timed meetings can be changed in this version.")
        plan = self.planner.plan(request, existing, history, place)
        if plan.action == "clarify":
            raise UserError(plan.question or "Please include a title, date, time and duration.")
        resolved = False
        if plan.action in ("update", "delete") and not existing:
            # No event ID: find the meeting by the title Gemini extracted from the request
            # or the conversation, or the one the user typed in reply to Dobby's question.
            wanted = (title or plan.target_title or "").strip()
            if not wanted:
                raise NeedTitle()
            matches = find_events(self.calendar, wanted, self.timezone)
            if not matches:
                raise UserError(say("no_matching_event", title=wanted))
            existing = clear_winner(matches)
            if existing is None:
                raise ChooseEvent(wanted, [e for _, e in matches[:3]])
            event_id, resolved = existing["id"], True
        if plan.action == "create" and existing:
            raise UserError("To create a meeting, leave event_id empty.")
        if existing and not existing.get("etag"):
            raise UserError("Event has no version information. Try refreshing /events.")
        emails, missing = self.resolve_invitees(plan.invitees)
        if missing and plan.action != "delete":
            raise NeedContacts(missing)
        body = (
            {}
            if plan.action == "delete"
            else event_body(plan, existing, self.timezone, list(emails.values()), place)
        )
        if plan.action != "delete":
            self.calendar.check_conflicts(body, event_id)
        return Proposal(
            plan.action,
            body,
            event_id,
            (existing or {}).get("etag"),
            sha256(str(interaction_id).encode()).hexdigest(),
            existing,
            tuple(a["email"] for a in body.get("attendees", [])),
            resolved,
        )
