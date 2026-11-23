"""The relay's SQL against real Postgres: cursors upsert, threads open and close, no text columns."""

import uuid
from datetime import datetime, timezone

import sqlalchemy as sa

from bot.integrations.instagram.relay import RelayStore

from .conftest import run_db


def test_relay_store_round_trip(migrated_db):
    store = RelayStore()
    guild = "guild-" + uuid.uuid4().hex
    thread = str(uuid.uuid4().int % 10**18)
    at = datetime(2026, 11, 22, 10, 0, tzinfo=timezone.utc)

    async def check(session):
        try:
            assert await store.conversation(session, guild, "c1") is None
            await store.save_cursor(session, guild, "c1", None, at)
            await store.save_cursor(session, guild, "c1", "m1", None)  # keeps the earlier time
            row = await store.conversation(session, guild, "c1")
            assert row["status"] == "closed" and row["last_message_id"] == "m1"
            assert row["last_message_at"] == at

            await store.open_thread(session, guild, "c1", "77", thread, "igsid-1")
            row = await store.by_thread(session, thread)
            assert row["status"] == "open" and row["participant_id"] == "igsid-1"
            assert row["discord_channel_id"] == "77"

            assert await store.close(session, thread) is True
            assert await store.close(session, thread) is False  # already closed
            assert (await store.by_thread(session, thread))["status"] == "closed"
        finally:
            await session.rollback()
            await session.execute(
                sa.text("DELETE FROM instagram_dm_threads WHERE guild_id = :g"), {"g": guild}
            )
            await session.commit()

    run_db(check)


def test_instagram_dm_threads_has_no_message_text_column(migrated_db):
    async def check(session):
        rows = await session.execute(
            sa.text(
                "SELECT column_name FROM information_schema.columns WHERE table_name = 'instagram_dm_threads'"
            )
        )
        columns = {r[0] for r in rows}
        assert {"conversation_id", "discord_thread_id", "last_message_id", "status"} <= columns
        assert not {"message", "text", "body", "content", "sender_name", "username"} & columns

    run_db(check)
