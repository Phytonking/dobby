"""Exercise durable email/invitation state with SQL, and mock only external calendar calls."""

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
import sqlalchemy as sa

from bot.integrations.base import RunContext
from bot.integrations.google_calendar.invitations import reconcile_invites, resolve_person
from bot.integrations.google_calendar.proposals import prepare_calendar_action
from bot.memory import save_calendar_email, set_calendar_email

MOD = "bot.integrations.google_calendar.invitations"
PROPOSALS = "bot.integrations.google_calendar.proposals"
EVENT = {
    "id": "event123",
    "summary": "Review",
    "start": {"dateTime": "2099-10-12T10:00:00+00:00"},
    "end": {"dateTime": "2099-10-12T11:00:00+00:00"},
    "attendees": [{"email": "existing@example.com"}],
}


class SQLiteSession:
    def __init__(self, engine):
        self.engine = engine

    async def __aenter__(self):
        self.connection = self.engine.connect()
        return self

    async def __aexit__(self, *args):
        self.connection.close()

    async def commit(self):
        self.connection.commit()

    async def execute(self, statement, *args, **kwargs):
        if "pg_advisory_xact_lock" in str(statement):
            # PostgreSQL's cross-process event lock has no SQLite equivalent.
            return None
        return self.connection.execute(statement, *args, **kwargs)


@asynccontextmanager
async def database():
    engine = sa.create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        for sql in (
            "CREATE TABLE users (id INTEGER PRIMARY KEY, discord_id TEXT UNIQUE, display_name TEXT NOT NULL, "
            "calendar_email TEXT, calendar_email_updated_at TIMESTAMP, role TEXT DEFAULT 'student')",
            "CREATE TABLE calendar_invites (id INTEGER PRIMARY KEY, guild_id TEXT, channel_id TEXT, "
            "requester_id TEXT, calendar_id TEXT, event_id TEXT, name_key TEXT, display_name TEXT, "
            "discord_id TEXT, status TEXT DEFAULT 'pending', created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, "
            "UNIQUE(guild_id, calendar_id, event_id, name_key))",
            "CREATE TABLE agent_actions (id INTEGER PRIMARY KEY, user_id INTEGER, discord_id TEXT, "
            "guild_id TEXT, channel_id TEXT, tool TEXT, status TEXT, duration_ms INTEGER)",
        ):
            conn.execute(sa.text(sql))

    def factory():
        return SQLiteSession(engine)

    try:
        with patch(f"{MOD}.SessionLocal", factory):
            yield factory
    finally:
        engine.dispose()


def bot():
    return SimpleNamespace(
        config=SimpleNamespace(guild=10, composio_entity="dobby", timezone="UTC"),
        toolset=object(),
        member_allowed=AsyncMock(return_value=True),
    )


async def draft(factory, client, people=None):
    async with factory() as session:
        ctx = RunContext(session, "10", "40", "1", client.toolset, client.config)
        params = {
            "summary": "Review",
            "start_datetime": "2099-10-12T10:00:00",
            "attendees": ["existing@example.com"],
            "deferred_invitees": people or [{"name": "Leonard", "discord_id": "2"}],
        }
        result = await prepare_calendar_action(ctx, "GOOGLECALENDAR_CREATE_EVENT", params)
        assert result["queued"]
        assert "**Waiting for email:** Leonard" in ctx.pending[0].preview
        assert "without waiting" in ctx.pending[0].preview
        return ctx.pending[0]


async def save(factory, name="Leonard", discord_id="2", email="leonard@example.com"):
    async with factory() as session:
        await save_calendar_email(session, discord_id, email, name)
        await session.commit()


@pytest.mark.parametrize("email_first", [False, True])
def test_scheduling_proceeds_and_later_email_invites_only_once(email_first):
    async def run():
        async with database() as factory:
            client = bot()
            with patch(
                f"{PROPOSALS}.run_action",
                new=AsyncMock(return_value={"success": True, "data": {"response_data": EVENT}}),
            ) as create:
                action = await draft(factory, client)
                create.assert_not_awaited()
                if email_first:
                    await save(factory)
                result = await action.execute()
                assert result["success"] and result["deferred_invites"]
                assert "deferred_invitees" not in create.await_args.args[2]
            if not email_first:
                with patch(f"{MOD}.run_action", new=AsyncMock()) as backend:
                    assert await reconcile_invites(client) == 0
                    backend.assert_not_awaited()
                await save(factory)
            with patch(
                f"{MOD}.run_action",
                new=AsyncMock(
                    side_effect=[
                        {"success": True, "data": {"response_data": EVENT}},
                        {"success": True},
                    ]
                ),
            ) as backend:
                assert await reconcile_invites(client) == 1
                assert await reconcile_invites(client) == 0
                assert backend.await_count == 2
                assert backend.await_args.args[1:] == (
                    "GOOGLECALENDAR_PATCH_EVENT",
                    {
                        "calendar_id": "primary",
                        "event_id": "event123",
                        "attendees": ["existing@example.com", "leonard@example.com"],
                        "send_updates": "all",
                    },
                    "dobby",
                )

    asyncio.run(run())


def test_unconfirmed_and_failed_events_create_no_deferred_invites():
    async def run():
        async with database() as factory:
            client = bot()
            action = await draft(factory, client)
            await save(factory)
            with patch(f"{MOD}.run_action", new=AsyncMock()) as backend:
                assert await reconcile_invites(client) == 0
                backend.assert_not_awaited()
            with patch(f"{PROPOSALS}.run_action", new=AsyncMock(return_value={"success": False})):
                assert not (await action.execute())["success"]
            async with factory() as session:
                assert (await session.execute(sa.text("SELECT COUNT(*) FROM calendar_invites"))).scalar() == 0

    asyncio.run(run())


def test_failed_invite_retries_without_losing_existing_guests_and_rechecks_access():
    async def run():
        async with database() as factory:
            client = bot()
            action = await draft(factory, client)
            with patch(
                f"{PROPOSALS}.run_action", new=AsyncMock(return_value={"success": True, "data": EVENT})
            ):
                await action.execute()
            await save(factory)
            with patch(
                f"{MOD}.run_action",
                new=AsyncMock(
                    side_effect=[
                        {"success": True, "data": EVENT},
                        {"success": False},
                        {"success": True, "data": EVENT},
                        {"success": True},
                    ]
                ),
            ) as backend:
                client.member_allowed.return_value = False
                assert await reconcile_invites(client) == 0
                backend.assert_not_awaited()
                client.member_allowed.return_value = True
                assert await reconcile_invites(client) == 0
                assert await reconcile_invites(client) == 1
                client.member_allowed.assert_awaited_with(1, 40, mention=False)

    asyncio.run(run())


def test_strict_resolution_rejects_ambiguous_names_but_accepts_discord_identity():
    async def run():
        async with database() as factory:
            await save(factory, "Leonard Park", "2")
            await save(factory, "Leonard Chen", "3", "chen@example.com")
            async with factory() as session:
                assert await resolve_person(session, {"name_key": "leonard"}) is None
                assert await resolve_person(session, {"name_key": "leonrd park"}) is None
                assert await resolve_person(session, {"name_key": "leonard park"}) == "leonard@example.com"
                assert (
                    await resolve_person(session, {"name_key": "anything", "discord_id": "3"})
                    == "chen@example.com"
                )

    asyncio.run(run())


def test_self_registration_preserves_role_and_old_context_cannot_overwrite_or_restore_email():
    async def run():
        async with database() as factory:
            await save(factory)
            async with factory() as session:
                await session.execute(sa.text("UPDATE users SET role = 'admin' WHERE discord_id = '2'"))
                await session.commit()
                assert await save_calendar_email(session, "2", "new@example.com", "Leonard")
                await session.commit()
                assert not await save_calendar_email(
                    session,
                    "2",
                    "old@example.com",
                    "Old name",
                    observed_at=datetime(2000, 1, 1, tzinfo=timezone.utc),
                )
                row = (await session.execute(sa.text("SELECT * FROM users"))).mappings().one()
                assert row["calendar_email"] == "new@example.com" and row["role"] == "admin"
                await save_calendar_email(
                    session, "2", "latest@example.com", "Discord nickname", replace_name=False
                )
                assert (
                    await session.execute(sa.text("SELECT display_name FROM users"))
                ).scalar() == "Leonard"
                await set_calendar_email(session, "2", None)
                await session.commit()
                assert not await save_calendar_email(
                    session,
                    "2",
                    "old@example.com",
                    "Old name",
                    observed_at=datetime(2000, 1, 1, tzinfo=timezone.utc),
                )
                assert (await session.execute(sa.text("SELECT calendar_email FROM users"))).scalar() is None

    asyncio.run(run())


@pytest.mark.parametrize("case", ["already_invited", "past", "cancelled"])
def test_does_not_reinvite_existing_guests_or_invite_to_finished_events(case):
    async def run():
        async with database() as factory:
            client = bot()
            action = await draft(factory, client)
            with patch(
                f"{PROPOSALS}.run_action", new=AsyncMock(return_value={"success": True, "data": EVENT})
            ):
                await action.execute()
            await save(factory)
            event = {**EVENT}
            if case == "already_invited":
                event["attendees"] = [{"email": "leonard@example.com"}]
            elif case == "past":
                event["end"] = {"dateTime": "2000-01-01T10:00:00+00:00"}
            else:
                event["status"] = "cancelled"
            with patch(
                f"{MOD}.run_action", new=AsyncMock(return_value={"success": True, "data": event})
            ) as backend:
                await reconcile_invites(client)
                backend.assert_awaited_once()
                assert backend.await_args.args[1] == "GOOGLECALENDAR_EVENTS_GET"
            async with factory() as session:
                status = (await session.execute(sa.text("SELECT status FROM calendar_invites"))).scalar()
                assert status == ("completed" if case == "already_invited" else "expired")

    asyncio.run(run())
