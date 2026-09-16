from datetime import datetime
from zoneinfo import ZoneInfo


class UserError(Exception):
    pass


class ConfigError(Exception):
    """Startup misconfiguration. Messages are authored here, so they are safe to display."""


def event_label(event, zone):
    """Confirmation text: "<event name>, <month>/<day>" in the team zone."""
    summary = " ".join(str(event.get("summary") or "(untitled)").split())
    start = event.get("start", {}).get("dateTime") or event.get("start", {}).get("date")
    try:
        when = datetime.fromisoformat(start)
        if when.tzinfo is not None:
            when = when.astimezone(ZoneInfo(zone))
        return f"{summary}, {when.month}/{when.day}"
    except (TypeError, ValueError):
        return summary
