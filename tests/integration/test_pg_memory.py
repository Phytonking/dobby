"""bot/memory.py raw SQL against real Postgres — upserts, timestamp guards, subselects."""

import uuid
from datetime import datetime, timedelta, timezone

import sqlalchemy as sa

from bot.memory import (
    find_user_by_discord_id,
    find_user_by_name,
    record_action,
    save_calendar_email,
    set_calendar_email,
)

from .conftest import run_db


def _id():
    return uuid.uuid4().hex


async def _add_user(session, name, email=None, discord_id=None):
    await session.execute(
        sa.text("INSERT INTO users (discord_id, display_name, calendar_email) VALUES (:d, :n, :e)"),
        {"d": discord_id or _id(), "n": name, "e": email},
    )


def test_save_calendar_email_inserts_then_updates_by_discord_id(migrated_db):
    discord_id = _id()

    async def check(session):
        assert await save_calendar_email(session, discord_id, "maya@uw.edu", "Maya")
        assert await save_calendar_email(session, discord_id, "maya@gmail.com", "Maya Lin")
        await session.commit()

        user = await find_user_by_discord_id(session, discord_id)
        assert user == {"display_name": "Maya Lin", "calendar_email": "maya@gmail.com"}

    run_db(check)


def test_older_observation_never_overwrites_a_newer_email(migrated_db):
    discord_id = _id()
    now = datetime.now(timezone.utc)

    async def check(session):
        await save_calendar_email(session, discord_id, "new@uw.edu", "Raj", observed_at=now)
        changed = await save_calendar_email(
            session, discord_id, "old@uw.edu", "Raj", observed_at=now - timedelta(hours=1)
        )
        await session.commit()

        assert changed is False
        assert (await find_user_by_discord_id(session, discord_id))["calendar_email"] == "new@uw.edu"

    run_db(check)


def test_manual_save_always_wins_and_can_keep_the_name(migrated_db):
    discord_id = _id()
    future = datetime.now(timezone.utc) + timedelta(days=1)

    async def check(session):
        await save_calendar_email(session, discord_id, "a@uw.edu", "Leonard", observed_at=future)
        await save_calendar_email(session, discord_id, "b@uw.edu", "Leo", replace_name=False)
        await session.commit()

        assert await find_user_by_discord_id(session, discord_id) == {
            "display_name": "Leonard",
            "calendar_email": "b@uw.edu",
        }

    run_db(check)


def test_set_calendar_email_reports_unregistered_and_clears(migrated_db):
    discord_id = _id()

    async def check(session):
        assert await set_calendar_email(session, _id(), "x@uw.edu") == 0
        await _add_user(session, "Priya", "p@uw.edu", discord_id)
        assert await set_calendar_email(session, discord_id, None) == 1
        await session.commit()

        assert (await find_user_by_discord_id(session, discord_id))["calendar_email"] is None

    run_db(check)


def test_find_user_by_name_matching_rules(migrated_db):
    def rand():
        return uuid.uuid4().hex[:6]

    zara, omar, ghost = f"Zara{rand()}", f"Omar{rand()}", f"Ghost{rand()}"

    async def check(session):
        await _add_user(session, f"{zara} Quill", f"{zara}@uw.edu")
        await _add_user(session, f"{omar} Alpha", f"{omar}a@uw.edu")
        await _add_user(session, f"{omar} Beta", f"{omar}b@uw.edu")
        await _add_user(session, f"{ghost} Nomail")  # no email: never a candidate
        await session.commit()

        found = await find_user_by_name(session, f"  {zara.upper()}   quill ")
        assert found["calendar_email"] == f"{zara}@uw.edu"  # exact, case/space-insensitive
        assert (await find_user_by_name(session, zara))[
            "calendar_email"
        ] == f"{zara}@uw.edu"  # unique first name
        assert await find_user_by_name(session, omar) is None  # shared first name is ambiguous
        assert (await find_user_by_name(session, f"{zara} Quil"))[
            "calendar_email"
        ] == f"{zara}@uw.edu"  # fuzzy
        assert await find_user_by_name(session, f"{ghost} Nomail") is None

    run_db(check)


def test_record_action_links_known_user_and_tolerates_unknown(migrated_db):
    known, unknown, guild = _id(), _id(), _id()

    async def check(session):
        await _add_user(session, "Audit Me", discord_id=known)
        for discord_id, status in ((known, "ok"), (unknown, "error")):
            await record_action(
                session,
                discord_id=discord_id,
                guild_id=guild,
                channel_id="c",
                tool="GOOGLECALENDAR_FIND_EVENT",
                status=status,
                duration_ms=12,
            )
        await session.commit()

        rows = (
            await session.execute(
                sa.text(
                    "SELECT a.discord_id, a.status, u.display_name FROM agent_actions a "
                    "LEFT JOIN users u ON u.id = a.user_id WHERE a.guild_id = :g ORDER BY a.status DESC"
                ),
                {"g": guild},
            )
        ).all()
        assert rows == [(known, "ok", "Audit Me"), (unknown, "error", None)]

    run_db(check)
