"""bot/memory.py raw SQL against real Postgres — jsonb casts, upserts, defaults."""

import uuid

import sqlalchemy as sa

from bot.memory import (
    append_turn,
    list_contacts,
    load_guild_settings,
    load_history,
    lookup_contact,
    save_contact,
)

from .conftest import run_db


def _gid():
    return uuid.uuid4().hex


def test_conversation_round_trip_preserves_jsonb(migrated_db):
    guild, channel = _gid(), "chan"

    async def check(session):
        await append_turn(session, guild, channel, role="user", content="schedule sync")
        await append_turn(
            session,
            guild,
            channel,
            role="tool",
            tool_name="GOOGLECALENDAR_CREATE_EVENT",
            tool_input={"summary": "Sync", "attendees": ["a@uw.edu"]},
            tool_result={"success": True, "data": {"id": "evt_1"}},
        )
        await append_turn(session, guild, channel, role="model", content="Done.")
        await session.commit()

        rows = await load_history(session, guild, channel)
        assert [r["role"] for r in rows] == ["user", "tool", "model"]
        tool_row = rows[1]
        # jsonb comes back as dicts — the agent loop feeds these straight
        # back into Gemini function responses.
        assert tool_row["tool_input"] == {"summary": "Sync", "attendees": ["a@uw.edu"]}
        assert tool_row["tool_result"] == {"success": True, "data": {"id": "evt_1"}}

    run_db(check)


def test_history_limit_returns_most_recent(migrated_db):
    guild, channel = _gid(), "chan"

    async def check(session):
        for i in range(5):
            await append_turn(session, guild, channel, role="user", content=f"msg {i}")
        await session.commit()

        rows = await load_history(session, guild, channel, limit=2)
        assert [r["content"] for r in rows] == ["msg 3", "msg 4"]

    run_db(check)


def test_contact_upsert_updates_in_place(migrated_db):
    guild = _gid()

    async def check(session):
        await save_contact(session, guild, "Maya Chen", "maya@uw.edu", added_by="1")
        await save_contact(session, guild, "  maya   chen ", "maya2@uw.edu", added_by="2")
        await session.commit()

        contacts = await list_contacts(session, guild)
        assert len(contacts) == 1
        assert contacts[0]["email"] == "maya2@uw.edu"

    run_db(check)


def test_contact_fuzzy_lookup(migrated_db):
    guild = _gid()

    async def check(session):
        await save_contact(session, guild, "Maya Chen", "maya@uw.edu")
        await session.commit()
        assert await lookup_contact(session, guild, "maya chen") == "maya@uw.edu"
        assert await lookup_contact(session, guild, "Maya") == "maya@uw.edu"
        assert await lookup_contact(session, guild, "zzz qqq") is None

    run_db(check)


def test_guild_settings_server_defaults_visible_to_bot(migrated_db):
    guild = _gid()

    async def check(session):
        await session.execute(sa.text("INSERT INTO guild_settings (guild_id) VALUES (:g)"), {"g": guild})
        await session.commit()

        settings = await load_guild_settings(session, guild)
        assert settings is not None
        assert settings["timezone"] == "America/Los_Angeles"
        assert settings["context_limit"] == 12
        assert settings["allowed_role_ids"] == []

    run_db(check)
