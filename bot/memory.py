"""Postgres lookups the bot needs: who people are, their calendar emails, and the audit trail.

No message content is stored here: channel context comes live from Discord (bot/context.py) and
the audit trail records which tool ran, not what it was asked or answered.
"""

from difflib import SequenceMatcher
from datetime import datetime, timezone

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession


def _normalize(name: str) -> str:
    return " ".join(str(name).split()).lower()


async def find_user_by_discord_id(session: AsyncSession, discord_id: str) -> dict | None:
    result = await session.execute(
        sa.text("SELECT display_name, calendar_email FROM users WHERE discord_id = :d"),
        {"d": str(discord_id)},
    )
    row = result.mappings().first()
    return dict(row) if row else None


async def find_user_by_name(session: AsyncSession, name: str) -> dict | None:
    """The user whose display name best matches `name`, or None.

    Exact match first, then a first name that belongs to exactly one person, then a fuzzy
    match. A first name shared by several people is ambiguous and returns None rather than a
    guess, since the result becomes a calendar invitation. Only users with a calendar email
    are candidates, since that is what callers need.
    """
    result = await session.execute(
        sa.text(
            "SELECT display_name, calendar_email FROM users "
            "WHERE calendar_email IS NOT NULL AND display_name IS NOT NULL"
        )
    )
    rows = [dict(r) for r in result.mappings().all()]
    key = _normalize(name)
    if not key:
        return None
    for row in rows:
        if _normalize(row["display_name"]) == key:
            return row
    by_first = [r for r in rows if _normalize(r["display_name"]).split(" ")[0] == key]
    if len(by_first) == 1:
        return by_first[0]
    if len(by_first) > 1:
        return None
    best_score, best_row = 0.0, None
    for row in rows:
        score = SequenceMatcher(None, key, _normalize(row["display_name"])).ratio()
        if score > best_score:
            best_score, best_row = score, row
    return best_row if best_score >= 0.6 else None


async def set_calendar_email(session: AsyncSession, discord_id: str, email: str | None) -> int:
    """Set or clear a user's calendar email. Returns rows changed: 0 means not registered."""
    result = await session.execute(
        sa.text(
            "UPDATE users SET calendar_email = :e, calendar_email_updated_at = CURRENT_TIMESTAMP WHERE discord_id = :d"
        ),
        {"e": email, "d": str(discord_id)},
    )
    return result.rowcount


async def save_calendar_email(
    session: AsyncSession,
    discord_id: str,
    email: str,
    display_name: str,
    *,
    observed_at=None,
    replace_name=True,
) -> bool:
    """Self-registration, keyed only by the authenticated Discord author. Never alter roles/login fields."""
    result = await session.execute(
        sa.text(
            "INSERT INTO users (discord_id, display_name, calendar_email, calendar_email_updated_at) "
            "VALUES (:d, :n, :e, :at) "
            "ON CONFLICT (discord_id) DO UPDATE SET calendar_email = :e, "
            "display_name = CASE WHEN :rename THEN :n ELSE users.display_name END, "
            "calendar_email_updated_at = :at WHERE users.calendar_email_updated_at IS NULL "
            "OR users.calendar_email_updated_at < :at OR :manual"
        ),
        {
            "d": str(discord_id),
            "n": display_name,
            "e": email,
            "at": observed_at or datetime.now(timezone.utc),
            "manual": observed_at is None,
            "rename": replace_name,
        },
    )
    return bool(result.rowcount)


async def record_action(
    session: AsyncSession,
    *,
    discord_id: str,
    guild_id: str,
    channel_id: str,
    tool: str,
    status: str,
    duration_ms: int,
) -> None:
    """One audit row per tool call (metadata only); the dashboard's audit page reads these."""
    await session.execute(
        sa.text(
            "INSERT INTO agent_actions "
            "(user_id, discord_id, guild_id, channel_id, tool, status, duration_ms) "
            "VALUES ((SELECT id FROM users WHERE discord_id = :d), :d, :g, :c, :tool, :status, :ms)"
        ),
        {
            "d": str(discord_id),
            "g": guild_id,
            "c": channel_id,
            "tool": tool,
            "status": status,
            "ms": duration_ms,
        },
    )
