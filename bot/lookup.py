"""Find the calendar event a user means when they only give a title, not an event ID."""

from datetime import datetime, timedelta
from difflib import SequenceMatcher
from zoneinfo import ZoneInfo

from .models import is_editable

MIN_SCORE = 0.5  # below this a title is not considered a match at all
CLEAR_SCORE = 0.8  # a single top match at or above this is accepted without asking
CLEAR_MARGIN = 0.15  # ...as long as it beats the runner-up by this much


def score(title, summary):
    a, b = " ".join(str(title).split()).lower(), " ".join(str(summary or "").split()).lower()
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    ratio = SequenceMatcher(None, a, b).ratio()
    if a in b or b in a:
        return max(0.9, ratio)
    return ratio


def find_events(calendar, title, timezone, days=60):
    """Editable upcoming events that resemble `title`, best first, as (score, event)."""
    now = datetime.now(ZoneInfo(timezone))
    events = calendar.list(now.isoformat(), (now + timedelta(days=days)).isoformat())
    scored = [(score(title, e.get("summary")), e) for e in events if is_editable(e)]
    scored = [(s, e) for s, e in scored if s >= MIN_SCORE]
    scored.sort(key=lambda item: (-item[0], item[1].get("start", {}).get("dateTime", "")))
    return scored


def clear_winner(matches):
    """The one event the user must mean, or None when it is ambiguous."""
    if not matches:
        return None
    if len(matches) == 1:
        return matches[0][1]
    top, second = matches[0][0], matches[1][0]
    if top >= CLEAR_SCORE and top - second >= CLEAR_MARGIN:
        return matches[0][1]
    return None
