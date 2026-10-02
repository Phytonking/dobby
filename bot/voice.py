"""Dobby's voice: one text file per situation, one phrasing per line.

`say()` reads the file for a key on every call, picks one line at random, formats it and
returns it. Nothing is cached, so the phrasings live in memory only for the duration of
the call. Lines starting with `#` and blank lines are ignored. A line may only use the
placeholders listed in FIELDS for its key; other lines are skipped as unusable.
"""

import logging
import random
import string
from pathlib import Path

log = logging.getLogger("scheduler")

RESPONSE_DIR = Path(__file__).parent / "responses"

# Placeholders each key is allowed to use. Keys absent here take no placeholders.
FIELDS = {
    "needs_help": {"question"},
    "email_shown": {"email"},
    "published": {"label"},
    "publish_failed": {"label"},
}

# Used when a file is missing or has no usable line. Must format with the same FIELDS.
FALLBACK = {
    "calendar_cancelled": "Dobby has cancelled the request. No calendar change made, Dobby promises.",
    "calendar_deleted": "Dobby has deleted the meeting, as you asked.",
    "calendar_updated": "Dobby has updated the meeting! Everything is in its proper place now.",
    "calendar_created": "Dobby has created the meeting! Happy to help, Dobby is.",
    "calendar_confirm_instructions": "React with 🟢 to confirm or 🔴 to cancel. Dobby is waiting.",
    "calendar_preview_outro": "Dobby will wait for your confirmation before changing anything. Confirm within 2 minutes. Times include their UTC offset.",
    "calendar_preview_intro": "Dobby has prepared a meeting proposal! Please check the details, if you please.",
    "working": "Dobby is on it! Dobby will reply here shortly…",
    "cooldown": "One moment, if you please! Dobby needs 10 seconds between requests.",
    "not_authorized": "Dobby is sorry, but you are not authorized to use this calendar here.",
    "needs_help": "Dobby needs your help with this, if you please. {question}",
    "generic_failure": (
        "Oh dear, Dobby could not complete the request. "
        "Check API quota, credentials and connectivity, then try again."
    ),
    "help_intro": "Dobby is happy to help with your meetings! Here is how to ask, if you please.",
    "email_saved": "Dobby has saved your calendar email. Dobby will invite you with it from now on!",
    "email_removed": "Dobby has cleared your calendar email, as you asked.",
    "email_shown": "Dobby invites you with {email}.",
    "email_none": "Dobby has no calendar email for you yet. Use `/email action:set email:you@uw.edu`.",
    "email_invalid": "Dobby could not read that as an email address. Try `you@uw.edu`.",
    "email_not_registered": (
        "Dobby does not have you on the roster yet. Ask an admin to add your Discord ID on the dashboard."
    ),
    "preview_intro": "Dobby has prepared this. Press Confirm to publish it or Cancel to discard it.",
    "published": "Dobby has published the {label}!",
    "publish_failed": "Dobby could not publish the {label}. Nothing went out; please try again later.",
    "cancelled": "Dobby has discarded the draft. Nothing was published.",
    "confirm_expired": "Dobby waited 2 minutes and heard nothing, so this draft has expired.",
    "confirm_not_requester": "Only the person who asked can confirm or cancel this, if you please.",
    "model_busy": "Dobby's magic is worn thin just now. Please ask again in a moment, if you please.",
    "model_rejected": (
        "Oh dear! Dobby's magic refused that request. This is Dobby's fault, not yours — "
        "an admin should check the bot logs."
    ),
    "no_answer": "Dobby's magic gave back nothing at all. Please ask again, perhaps more simply?",
    "done": "Dobby has done it!",
    "tool_limit": (
        "Oh dear, Dobby tried a great many things and must stop. Please break the request into smaller steps."
    ),
}


def placeholders(line):
    return {name for _, name, _, _ in string.Formatter().parse(line) if name}


def load_lines(key):
    """Every usable phrasing for a key, freshly read from disk."""
    try:
        text = (RESPONSE_DIR / f"{key}.txt").read_text(encoding="utf-8")
    except OSError:
        return []
    allowed = FIELDS.get(key, set())
    lines = []
    for raw in text.splitlines():
        line = raw.strip()
        if line and not line.startswith("#") and placeholders(line) <= allowed:
            lines.append(line)
    return lines


def pick(lines):
    return random.choice(lines)


def say(key, **fields):
    lines = load_lines(key)
    if not lines:
        log.warning("voice_missing key=%s", key)
        template = FALLBACK.get(key, "Dobby is at your service.")
    else:
        template = pick(lines)
    try:
        return template.format(**fields)
    except (KeyError, IndexError, ValueError):
        log.warning("voice_format_failed key=%s", key)
        return FALLBACK.get(key, "Dobby is at your service.").format_map(Defaults(fields))


class Defaults(dict):
    def __missing__(self, _key):
        return ""
