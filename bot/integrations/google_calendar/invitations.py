"""Durable, already-approved invitations waiting for a person's email."""

import logging
from datetime import datetime, timezone

import sqlalchemy as sa

from ...composio import run_action
from ...db import SessionLocal
from ...memory import record_action
from ...models import valid_email

log = logging.getLogger("calendar_invites")


def name_key(name):
    return " ".join(name.lstrip("@").split()).casefold()


async def remember_invites(ctx, calendar_id, event_id, people):
    async with SessionLocal() as session:
        for person in people:
            await session.execute(
                sa.text(
                    "INSERT INTO calendar_invites "
                    "(guild_id, channel_id, requester_id, calendar_id, event_id, name_key, display_name, discord_id) "
                    "VALUES (:g, :c, :r, :cal, :event, :key, :name, :d) "
                    "ON CONFLICT (guild_id, calendar_id, event_id, name_key) DO NOTHING"
                ),
                {
                    "g": ctx.guild_id,
                    "c": ctx.channel_id,
                    "r": ctx.discord_user_id,
                    "cal": calendar_id,
                    "event": event_id,
                    "key": name_key(person["name"]),
                    "name": person["name"],
                    "d": person.get("discord_id") or None,
                },
            )
        await session.commit()


async def resolve_person(session, person):
    """Exact Discord identity or an unambiguous name; never fuzzy-match automatic invitations."""
    rows = (
        (await session.execute(sa.text("SELECT discord_id, display_name, calendar_email FROM users")))
        .mappings()
        .all()
    )
    if person.get("discord_id"):
        matches = [r for r in rows if r["discord_id"] == person["discord_id"]]
    else:
        key = person["name_key"]
        matches = [r for r in rows if name_key(r["display_name"]) == key]
        if not matches:
            matches = [r for r in rows if name_key(r["display_name"]).split(" ")[0] == key]
    if len(matches) == 1 and valid_email(matches[0]["calendar_email"]):
        return matches[0]["calendar_email"].lower()
    return None


async def reconcile_invites(bot):
    """Retry pending invitations. Serialize each event so concurrent email replies do not lose guests."""
    from .proposals import event_from

    completed = 0
    async with SessionLocal() as session:
        waiting = (
            (
                await session.execute(
                    sa.text(
                        "SELECT * FROM calendar_invites WHERE guild_id = :g AND status = 'pending' ORDER BY created_at"
                    ),
                    {"g": str(bot.config.guild)},
                )
            )
            .mappings()
            .all()
        )
    for row in waiting:
        if not await bot.member_allowed(int(row["requester_id"]), int(row["channel_id"]), mention=False):
            continue
        try:
            async with SessionLocal() as session:
                await session.execute(
                    sa.text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
                    {"key": f"calendar-invite:{row['guild_id']}:{row['calendar_id']}:{row['event_id']}"},
                )
                current = (
                    (
                        await session.execute(
                            sa.text("SELECT * FROM calendar_invites WHERE id = :id AND status = 'pending'"),
                            {"id": row["id"]},
                        )
                    )
                    .mappings()
                    .first()
                )
                if current is None:
                    continue
                email = await resolve_person(session, current)
                if email is None:
                    continue
                result = await run_action(
                    bot.toolset,
                    "GOOGLECALENDAR_EVENTS_GET",
                    {
                        "calendar_id": row["calendar_id"],
                        "event_id": row["event_id"],
                    },
                    bot.config.composio_entity,
                )
                event = event_from(result.get("data"), row["event_id"]) if result.get("success") else None
                if not event:
                    continue
                end = event.get("end", {}).get("dateTime")
                expired = event.get("status") == "cancelled" or (
                    end and datetime.fromisoformat(end).astimezone(timezone.utc) <= datetime.now(timezone.utc)
                )
                if expired:
                    await session.execute(
                        sa.text("UPDATE calendar_invites SET status = 'expired' WHERE id = :id"),
                        {"id": row["id"]},
                    )
                    await session.commit()
                    continue
                attendees = [a["email"] for a in event.get("attendees", []) if a.get("email")]
                if email not in {a.lower() for a in attendees}:
                    result = await run_action(
                        bot.toolset,
                        "GOOGLECALENDAR_PATCH_EVENT",
                        {
                            "calendar_id": row["calendar_id"],
                            "event_id": row["event_id"],
                            "attendees": attendees + [email],
                            "send_updates": "all",
                        },
                        bot.config.composio_entity,
                    )
                    await record_action(
                        session,
                        discord_id=row["requester_id"],
                        guild_id=row["guild_id"],
                        channel_id=row["channel_id"],
                        tool="google_calendar.deferred_invite",
                        status="ok" if result.get("success") else "error",
                        duration_ms=0,
                    )
                    if not result.get("success"):
                        await session.commit()
                        continue
                await session.execute(
                    sa.text("UPDATE calendar_invites SET status = 'completed' WHERE id = :id"),
                    {"id": row["id"]},
                )
                await session.commit()
                completed += 1
        except Exception as exc:
            log.warning("deferred_invite_failed type=%s", type(exc).__name__)
    return completed


def created_event_id(result):
    data = result.get("data")
    while isinstance(data, dict):
        if isinstance(data.get("id"), str) and "start" in data:
            return data["id"]
        data = data.get("response_data", data.get("data"))
    return None
