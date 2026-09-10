"""Human-readable pieces for Dobby's preview and confirmation messages."""

from datetime import datetime
from zoneinfo import ZoneInfo

ARROW = " → "


def parse(value, zone):
    try:
        when = datetime.fromisoformat(value)
    except TypeError, ValueError:
        return None
    return when.astimezone(ZoneInfo(zone)) if when.tzinfo is not None else when


def clock(when):
    return when.strftime("%I:%M %p").lstrip("0")


def day(when):
    return f"{when.strftime('%a %b')} {when.day}, {when.year}"


def offset(when):
    raw = when.strftime("%z")  # -0600
    return f"UTC{raw[:3]}:{raw[3:]}" if raw else "local time"


def when(start, end, zone):
    """'Sat Oct 12, 2030 · 2:00 PM – 3:00 PM (UTC-06:00)', or a two-day span, or raw text."""
    a, b = parse(start, zone), parse(end, zone)
    if a is None:
        return str(start or "")
    if b is None:
        return f"{day(a)} · {clock(a)} ({offset(a)})"
    if a.date() == b.date():
        return f"{day(a)} · {clock(a)} – {clock(b)} ({offset(a)})"
    return f"{day(a)} {clock(a)}{ARROW}{day(b)} {clock(b)} ({offset(a)})"


def emails(event):
    return [a["email"].lower() for a in event.get("attendees", []) if a.get("email")]


def changes(existing, body, zone):
    """Old → new for every field an update touches, as 'Label: old → new' strings."""
    existing = existing or {}
    out = []
    labels = {"summary": "Title", "description": "Description", "location": "Location"}
    for key, label in labels.items():
        if key in body and body[key] != existing.get(key):
            out.append(f"{label}: {existing.get(key) or '(none)'}{ARROW}{body[key] or '(none)'}")
    if "start" in body or "end" in body:
        before = when(
            existing.get("start", {}).get("dateTime"), existing.get("end", {}).get("dateTime"), zone
        )
        after = when(
            body.get("start", existing.get("start", {})).get("dateTime"),
            body.get("end", existing.get("end", {})).get("dateTime"),
            zone,
        )
        if before != after:
            out.append(f"When: {before or '(none)'}{ARROW}{after}")
    if "attendees" in body:
        added = [e for e in emails(body) if e not in set(emails(existing))]
        if added:
            out.append("Invitees added: " + ", ".join(added))
    return out
