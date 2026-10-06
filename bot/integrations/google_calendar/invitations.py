"""Invitations waiting for a person's email, and the approval step that adds them.

A confirmed meeting may name people whose calendar email Dobby does not have yet. Each becomes a
``calendar_invites`` row keyed by their exact Discord ID. When the email arrives Dobby does not add
anyone by itself: it posts the address it found and the meeting in the requester's original channel,
and only the requester's 🟢 patches the guest list.

Row status: ``pending`` (waiting for an email, or for the next try) → ``proposed`` (an approval prompt
is live) → ``completed`` or ``declined``; ``expired`` when the meeting is over, the row is old, or
nobody answered after several prompts.
"""

import logging
from datetime import datetime, timedelta, timezone

import sqlalchemy as sa

from ...composio import run_action
from ...db import SessionLocal
from ...memory import fitting_users
from ...models import valid_email
from ...voice import say
from ..base import PendingAction
from . import format as fmt
from .proposals import event_from, event_over, safe

log = logging.getLogger("calendar_invites")

APPROVAL_SECONDS = 900  # how long an approval prompt stays open (the response text says 15 minutes)
STALE_AFTER = timedelta(seconds=APPROVAL_SECONDS + 60)  # a "proposed" row older than this lost its prompt
RETRY_AFTER = timedelta(hours=1)  # an unanswered or failed approval is asked again no sooner than this
MAX_TRIES = 3  # prompts or failed attempts per invitation before Dobby stops asking
KEEP_FOR = timedelta(days=90)  # nothing waits longer than this


def name_key(name):
    return " ".join(name.lstrip("@").split()).casefold()


def utcnow():
    return datetime.now(timezone.utc)


async def execute_sql(sql, session=None, **params):
    """One statement; returns the rows it changed. Without a `session` it commits in its own transaction."""
    if session is not None:
        return (await session.execute(sa.text(sql), params)).rowcount
    async with SessionLocal() as own:
        result = await own.execute(sa.text(sql), params)
        await own.commit()
        return result.rowcount


async def remember_invites(ctx, calendar_id, event_id, people):
    """Save people whose invitation must wait for an email. Called only after a confirmed event write."""
    async with SessionLocal() as session:
        for person in people:
            discord_id = person.get("discord_id") or None
            if discord_id:
                # A row saved before IDs were required matched this name; the exact ID replaces it.
                await session.execute(
                    sa.text(
                        "UPDATE calendar_invites SET status = 'expired' WHERE guild_id = :g AND calendar_id = :cal "
                        "AND event_id = :event AND discord_id IS NULL AND name_key = :legacy "
                        "AND status IN ('pending', 'proposed')"
                    ),
                    {
                        "g": ctx.guild_id,
                        "cal": calendar_id,
                        "event": event_id,
                        "legacy": name_key(person["name"]),
                    },
                )
            await session.execute(
                sa.text(
                    "INSERT INTO calendar_invites "
                    "(guild_id, channel_id, requester_id, calendar_id, event_id, name_key, display_name, discord_id) "
                    "VALUES (:g, :c, :r, :cal, :event, :key, :name, :d) "
                    "ON CONFLICT (guild_id, calendar_id, event_id, name_key) DO UPDATE SET "
                    "status = 'pending', proposed_at = NULL, attempts = 0, channel_id = :c, requester_id = :r, "
                    "display_name = :name, discord_id = :d "
                    "WHERE calendar_invites.status NOT IN ('pending', 'proposed')"
                ),
                {
                    "g": ctx.guild_id,
                    "c": ctx.channel_id,
                    "r": ctx.discord_user_id,
                    "cal": calendar_id,
                    "event": event_id,
                    # One row per person: two guests named Sam are different Discord IDs.
                    "key": f"id:{discord_id}" if discord_id else name_key(person["name"]),
                    "name": person["name"],
                    "d": discord_id,
                },
            )
        await session.commit()


async def resolve_person(session, row, directory=None):
    """The registered person a waiting invitation is for, once they have a valid calendar email.

    Rows carry an exact Discord ID. Rows saved before IDs were required matched a typed name, so
    they still resolve by an unambiguous name (the same rule as the lookup tool); either way the
    requester sees the address before anything is added. `directory` caches the user list for a run.
    """
    if row.get("discord_id"):
        found = (
            (
                await session.execute(
                    sa.text("SELECT display_name, calendar_email FROM users WHERE discord_id = :d"),
                    {"d": row["discord_id"]},
                )
            )
            .mappings()
            .all()
        )
    else:
        if directory is None:
            directory = {}
        if "everyone" not in directory:
            everyone = await session.execute(sa.text("SELECT display_name, calendar_email FROM users"))
            directory["everyone"] = [dict(u) for u in everyone.mappings().all()]
        found = fitting_users(row["name_key"], directory["everyone"])
    if len(found) == 1 and valid_email(found[0]["calendar_email"]):
        return found[0]
    return None


async def tidy(session, guild, now):
    """Reopen approvals whose prompt is gone (restart, crash) and retire invitations nobody can act on."""
    await session.execute(
        sa.text(
            "UPDATE calendar_invites SET status = 'pending' "
            "WHERE guild_id = :g AND status = 'proposed' AND proposed_at < :stale"
        ),
        {"g": guild, "stale": now - STALE_AFTER},
    )
    await session.execute(
        sa.text(
            "UPDATE calendar_invites SET status = 'expired' WHERE guild_id = :g AND "
            "((status = 'pending' AND attempts >= :tries) OR (status IN ('pending', 'proposed') AND created_at < :old))"
        ),
        {"g": guild, "tries": MAX_TRIES, "old": now - KEEP_FOR},
    )


async def claim(row_id, now):
    """Take a waiting invitation for one approval prompt. False when someone else already did."""
    return (
        await execute_sql(
            "UPDATE calendar_invites SET status = 'proposed', proposed_at = :now, attempts = attempts + 1 "
            "WHERE id = :id AND status = 'pending' AND (proposed_at IS NULL OR proposed_at < :retry)",
            id=row_id,
            now=now,
            retry=now - RETRY_AFTER,
        )
        == 1
    )


async def backoff(row_id, now):
    """Count a failed attempt and hold off retrying; after MAX_TRIES the row expires."""
    await execute_sql(
        "UPDATE calendar_invites SET proposed_at = :now, attempts = attempts + 1 "
        "WHERE id = :id AND status = 'pending'",
        id=row_id,
        now=now,
    )


async def settle(row_id, status, was, session=None):
    await execute_sql(
        "UPDATE calendar_invites SET status = :status WHERE id = :id AND status = :was",
        session,
        id=row_id,
        status=status,
        was=was,
    )


async def reopen(row_id, session=None, *, now=None, immediate=False):
    """Give a proposed invitation back to the queue (prompt expired or the add failed).

    It waits out the retry cooldown unless `immediate`: the requester answered, so this was not an
    unanswered prompt and should neither wait nor count as a try.
    """
    if immediate:
        await execute_sql(
            "UPDATE calendar_invites SET status = 'pending', proposed_at = NULL, "
            "attempts = CASE WHEN attempts > 0 THEN attempts - 1 ELSE 0 END "
            "WHERE id = :id AND status = 'proposed'",
            session,
            id=row_id,
        )
        return
    await execute_sql(
        "UPDATE calendar_invites SET status = 'pending', proposed_at = :now WHERE id = :id AND status = 'proposed'",
        session,
        id=row_id,
        now=now or utcnow(),
    )


async def load_event(bot, row):
    result = await run_action(
        bot.toolset,
        "GOOGLECALENDAR_EVENTS_GET",
        {"calendar_id": row["calendar_id"], "event_id": row["event_id"]},
        bot.config.composio_entity,
    )
    return event_from(result.get("data"), row["event_id"]) if result.get("success") else None


async def add_guest(bot, row, email, guest):
    """Run an approved addition, re-checking what could have changed since the requester looked.

    Every status change here uses the connection that holds the event lock, so a busy pool cannot
    deadlock a lock holder against the approvals waiting on it.
    """
    async with SessionLocal() as session:
        # Serialize guest-list edits to one event so two approvals cannot overwrite each other.
        await session.execute(
            sa.text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
            {"key": f"calendar-invite:{row['guild_id']}:{row['calendar_id']}:{row['event_id']}"},
        )
        current = (
            (
                await session.execute(
                    sa.text("SELECT * FROM calendar_invites WHERE id = :id AND status = 'proposed'"),
                    {"id": row["id"]},
                )
            )
            .mappings()
            .first()
        )
        if current is None:
            return {
                "success": False,
                "message": "That invitation is no longer waiting, so Dobby changed nothing.",
            }
        person = await resolve_person(session, current)
        if person is None or person["calendar_email"].lower() != email:
            await reopen(row["id"], session, immediate=True)
            await session.commit()
            return {
                "success": False,
                "message": "That person's email changed after the preview, so Dobby invited no one. "
                "Dobby will ask again with the new address.",
            }
        event = await load_event(bot, row)
        if event is None:
            await reopen(row["id"], session)
            await session.commit()
            return {"success": False}
        if event_over(event):
            await settle(row["id"], "expired", "proposed", session)
            await session.commit()
            return {
                "success": False,
                "message": "That meeting is over or cancelled, so Dobby invited no one.",
            }
        if email not in fmt.emails(event):
            guests = [a["email"] for a in event.get("attendees", []) if a.get("email")]
            patched = await run_action(
                bot.toolset,
                "GOOGLECALENDAR_PATCH_EVENT",
                {
                    "calendar_id": row["calendar_id"],
                    "event_id": row["event_id"],
                    "attendees": guests + [email],
                    "send_updates": "all",
                },
                bot.config.composio_entity,
            )
            if not patched.get("success"):
                await reopen(row["id"], session)
                await session.commit()
                return {"success": False}
        await settle(row["id"], "completed", "proposed", session)
        await session.commit()
        return {"success": True, "message": say("calendar_updated") + f"\n\n**Invited:** {safe(guest)}"}


def approval_action(bot, row, person, event):
    """The prompt for one invitation: what the requester sees, and what 🟢 / 🔴 / silence do to the row."""
    row_id = row["id"]
    email = person["calendar_email"].lower()
    guest = f"{person['display_name']} ({email})"
    when = fmt.when(
        event.get("start", {}).get("dateTime"), event.get("end", {}).get("dateTime"), bot.config.timezone
    )
    lines = [
        f"**Guest:** {safe(guest)}",
        f"**Meeting:** {safe(event.get('summary') or '(untitled)')}",
        f"**When:** {safe(when)}",
    ]
    if event.get("attendees"):
        lines.append(f"_Google will notify the {len(event['attendees'])} existing invitees._")

    async def execute():
        try:
            return await add_guest(bot, row, email, guest)
        except Exception as exc:
            log.warning("invite_apply_failed type=%s", type(exc).__name__)
            await reopen(row_id)
            return {"success": False}

    async def release(outcome):
        if outcome == "cancelled":
            await settle(row_id, "declined", "proposed")
        else:
            await reopen(row_id)

    return PendingAction(
        "google_calendar", f"Invite {safe(person['display_name'])}", "\n".join(lines), execute, release
    )


async def propose(bot, row, person, now):
    """An approval for adding `person` to the row's meeting, or None when nothing should be asked."""
    event = await load_event(bot, row)
    if event is None:  # provider hiccup or a deleted meeting: try again later, a few times
        await backoff(row["id"], now)
        return None
    if event_over(event):
        await settle(row["id"], "expired", "pending")
        return None
    if person["calendar_email"].lower() in fmt.emails(event):
        await settle(row["id"], "completed", "pending")
        return None
    # Stamp the claim with the current time, not the batch's: the prompt opens only after every
    # row's event loads, and the stale sweep times the prompt from proposed_at.
    if not await claim(row["id"], utcnow()):
        return None
    return approval_action(bot, row, person, event)


async def reconcile_invites(bot):
    """Ask requesters to approve invitations whose email has arrived; returns how many were asked about.

    This never edits a calendar. Resolving who a row is for is database-only, and Discord and the
    calendar are touched only for rows that resolved.
    """
    now = utcnow()
    guild = str(bot.config.guild)
    async with SessionLocal() as session:
        await tidy(session, guild, now)
        waiting = (
            (
                await session.execute(
                    sa.text(
                        "SELECT * FROM calendar_invites WHERE guild_id = :g AND status = 'pending' "
                        "AND (proposed_at IS NULL OR proposed_at < :retry) ORDER BY created_at"
                    ),
                    {"g": guild, "retry": now - RETRY_AFTER},
                )
            )
            .mappings()
            .all()
        )
        ready, directory = [], {}
        for row in waiting:
            person = await resolve_person(session, row, directory)
            if person is not None:
                ready.append((dict(row), person))
        await session.commit()

    allowed, prompts = {}, {}
    for row, person in ready:
        asker = (row["requester_id"], row["channel_id"])
        try:
            if asker not in allowed:
                allowed[asker] = await bot.member_allowed(int(asker[0]), int(asker[1]), mention=False)
            if not allowed[asker]:
                await backoff(row["id"], now)
                continue
            action = await propose(bot, row, person, now)
        except Exception as exc:
            log.warning("invite_proposal_failed type=%s", type(exc).__name__)
            continue
        if action is not None:
            prompts.setdefault(asker, []).append(action)

    asked = 0
    for (requester, channel), actions in prompts.items():
        try:
            posted = await bot.ask_calendar_approval(int(channel), int(requester), actions)
        except Exception as exc:
            log.warning("invite_approval_failed type=%s", type(exc).__name__)
            posted = False
        if posted:
            asked += len(actions)
            continue
        for action in actions:
            try:
                await action.release("expired")
            except Exception as exc:  # the stale-prompt sweep reopens it if this cannot
                log.warning("invite_release_failed type=%s", type(exc).__name__)
    return asked


def created_event_id(result):
    data = result.get("data")
    while isinstance(data, dict):
        if isinstance(data.get("id"), str) and "start" in data:
            return data["id"]
        data = data.get("response_data", data.get("data"))
    return None
