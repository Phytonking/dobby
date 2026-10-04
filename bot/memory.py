"""Postgres lookups the bot needs: who people are, their calendar emails, and the audit trail.

No message content is stored here: channel context comes live from Discord (bot/context.py) and
the audit trail records which tool ran, not what it was asked or answered.
"""

from dataclasses import dataclass
from difflib import SequenceMatcher
from datetime import datetime, timezone

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession


CLOSE_MATCH = 0.6  # similarity at which a name is worth suggesting (never auto-resolved)


def _normalize(name: str) -> str:
    return " ".join(str(name).lstrip("@").split()).casefold()


def fitting_users(name: str, users) -> list:
    """The users a typed name could mean: it equals their full display name or their first word.

    The one rule shared by lookups and late invitations, so they can never disagree about who "Sam" is.
    """
    key = _normalize(name)
    if not key:
        return []
    fits = []
    for user in users:
        full = _normalize(user["display_name"])
        if key in (full, full.split(" ")[0]):
            fits.append(user)
    return fits


async def find_user_by_discord_id(session: AsyncSession, discord_id: str) -> dict | None:
    result = await session.execute(
        sa.text("SELECT display_name, calendar_email FROM users WHERE discord_id = :d"),
        {"d": str(discord_id)},
    )
    row = result.mappings().first()
    return dict(row) if row else None


@dataclass(frozen=True)
class NameMatch:
    """What a typed name points at. `user` is set only when exactly one person fits."""

    user: dict | None = None
    # Display names: the people a tied name could mean, or close guesses for the requester to confirm.
    candidates: tuple[str, ...] = ()
    ambiguous: bool = False


async def match_user_by_name(session: AsyncSession, name: str) -> NameMatch:
    """Resolve a typed name to one registered person, never by guessing.

    A name fits a person when it equals their full display name or their first name, and everyone
    registered is considered, with or without an email. One fit is a match; several are ambiguous
    and the caller must ask which. Near misses only ever come back as `candidates` to confirm,
    because the result becomes a calendar invitation and a wrong guess invites the wrong person.
    """
    key = _normalize(name)
    if not key:
        return NameMatch()
    result = await session.execute(
        sa.text(
            "SELECT discord_id, display_name, calendar_email FROM users "
            "WHERE display_name IS NOT NULL ORDER BY display_name, discord_id"
        )
    )
    rows = [dict(r) for r in result.mappings().all()]
    fits = fitting_users(key, rows)
    if len(fits) == 1:
        return NameMatch(user=fits[0])
    if fits:
        return NameMatch(candidates=tuple(dict.fromkeys(r["display_name"] for r in fits)), ambiguous=True)
    scored = []
    for row in rows:
        full = _normalize(row["display_name"])
        score = max(
            SequenceMatcher(None, key, full).ratio(), SequenceMatcher(None, key, full.split(" ")[0]).ratio()
        )
        if score >= CLOSE_MATCH:
            scored.append((score, row["display_name"]))
    scored.sort(key=lambda pair: -pair[0])
    return NameMatch(candidates=tuple(dict.fromkeys(label for _, label in scored))[:3])


async def display_names_for_emails(session: AsyncSession, emails) -> dict[str, str]:
    """{lowercase email: display name} for registered people, so previews can say who an address is."""
    wanted = sorted({e.lower() for e in emails if e})
    if not wanted:
        return {}
    result = await session.execute(
        sa.text(
            "SELECT lower(calendar_email) AS email, display_name FROM users "
            "WHERE lower(calendar_email) IN :emails ORDER BY display_name"
        ).bindparams(sa.bindparam("emails", expanding=True)),
        {"emails": wanted},
    )
    names: dict[str, str] = {}
    for row in result.mappings().all():
        names.setdefault(row["email"], row["display_name"])
    return names


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
