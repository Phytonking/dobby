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
    "context_used": {"count"},
    "events_found": {"count", "days"},
    "ask_email": {"names"},
    "contact_saved": {"name"},
    "contact_removed": {"name"},
    "contact_unknown": {"name"},
    "no_matching_event": {"title"},
    "confirm_event_match": {"title", "start"},
}

# Used when a file is missing or has no usable line. Must format with the same FIELDS.
FALLBACK = {
    "working": "Dobby is on it! Dobby will prepare a meeting preview here…",
    "preview_intro": "Dobby has prepared a meeting proposal! Please check the details, if you please.",
    "preview_outro": (
        "Dobby will wait for your confirmation before changing anything. "
        "Confirm within 2 minutes. Times include their UTC offset."
    ),
    "confirm_instructions": "React with 🟢 to confirm or 🔴 to cancel. Dobby is waiting.",
    "confirm_expired": "Dobby waited 2 minutes and heard nothing, so this preview has expired.",
    "context_used": "Used {count} recent text messages from the requesting channel.",
    "permission_missing": (
        "Dobby needs permission to send messages, send messages in threads, add reactions, "
        "and read history in this channel."
    ),
    "cooldown": "One moment, if you please! Dobby needs 10 seconds between requests.",
    "not_authorized": "Dobby is sorry, but you are not authorized to use this calendar here.",
    "busy": "The bot is busy. Please try again shortly.",
    "created": "Dobby has created the meeting! Happy to help, Dobby is.",
    "updated": "Dobby has updated the meeting! Everything is in its proper place now.",
    "deleted": "Dobby has deleted the meeting, as you asked.",
    "cancelled": "Dobby has cancelled the request. No calendar change made, Dobby promises.",
    "confirm_unusable": (
        "Dobby cannot use this confirmation, sorry! It is not authorized, already used, or expired."
    ),
    "apply_failed_suffix": "Check /events before retrying; a network failure may occur after a write.",
    "needs_help": "Dobby needs your help with this, if you please. {question}",
    "generic_failure": (
        "Oh dear, Dobby could not complete the request. "
        "Check API quota, credentials and connectivity, then try again."
    ),
    "events_found": "Dobby found {count} events in the next {days} days! Full IDs in attachment.",
    "events_none": "Dobby found no upcoming events. A little breathing room for everyone!",
    "help_intro": "Dobby is happy to help with your meetings! Here is how to ask, if you please.",
    "ask_email": "Dobby does not know an email for {names}. Reply here with it, if you please.",
    "contact_saved": "Dobby has remembered {name}'s email. Dobby never forgets a friend!",
    "contact_invalid_email": "Dobby could not find an email address in that. Try `Name: name@example.com`.",
    "contact_removed": "Dobby has forgotten {name}'s email, as you asked.",
    "contact_unknown": "Dobby has no saved email for {name}.",
    "contacts_empty": "Dobby has not learned any emails yet.",
    "contacts_list_intro": "Here are the people Dobby knows, if you please:",
    "ask_title": "Which meeting does Dobby need? Reply with its title, if you please.",
    "choose_event_intro": "Dobby found more than one likely meeting. Reply with the number, if you please:",
    "no_matching_event": "Dobby searched but found no upcoming meeting like “{title}”.",
    "confirm_event_match": "Dobby found “{title}” starting {start}. Is this the right meeting?",
    "capabilities_intro": "Dobby would be delighted to explain! Here is what Dobby can do for you.",
    "chat_fallback": "Dobby is not quite sure how to answer that, but Dobby is very happy you asked!",
    "farewell": "Dobby is going now! Dobby has socks to fold and a calendar to guard.",
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
    except KeyError, IndexError, ValueError:
        log.warning("voice_format_failed key=%s", key)
        return FALLBACK.get(key, "Dobby is at your service.").format_map(Defaults(fields))


class Defaults(dict):
    def __missing__(self, _key):
        return ""
