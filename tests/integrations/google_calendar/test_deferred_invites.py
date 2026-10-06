"""Late invitations: durable state in SQL, Composio and Discord mocked.

A person whose email is unknown can be listed on a confirmed meeting. When their email arrives Dobby
asks the requester to approve it; nothing reaches the guest list without that approval.
"""

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
import sqlalchemy as sa

from bot.integrations.base import RunContext
from bot.integrations.google_calendar import invitations
from bot.integrations.google_calendar.invitations import (
    APPROVAL_SECONDS,
    KEEP_FOR,
    MAX_TRIES,
    RETRY_AFTER,
    STALE_AFTER,
    reconcile_invites,
    resolve_person,
)
from bot.integrations.google_calendar.proposals import prepare_calendar_action
from bot.memory import save_calendar_email, set_calendar_email

MOD = "bot.integrations.google_calendar.invitations"
PROPOSALS = "bot.integrations.google_calendar.proposals"
CREATE = "GOOGLECALENDAR_CREATE_EVENT"
GET, PATCH = "GOOGLECALENDAR_EVENTS_GET", "GOOGLECALENDAR_PATCH_EVENT"
EVENT = {
    "id": "event123",
    "summary": "Review",
    "start": {"dateTime": "2099-10-12T10:00:00+00:00"},
    "end": {"dateTime": "2099-10-12T11:00:00+00:00"},
    "attendees": [{"email": "existing@example.com"}],
}
GOT = {"success": True, "data": {"response_data": EVENT}}


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


class Clock:
    """Controllable time for the retry and expiry rules."""

    def __init__(self):
        self.now = datetime.now(timezone.utc)

    def __call__(self):
        return self.now

    def advance(self, delta):
        self.now += delta


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
            "proposed_at TIMESTAMP, attempts INTEGER NOT NULL DEFAULT 0, "
            "UNIQUE(guild_id, calendar_id, event_id, name_key))",
            "CREATE TABLE agent_actions (id INTEGER PRIMARY KEY, user_id INTEGER, discord_id TEXT, "
            "guild_id TEXT, channel_id TEXT, tool TEXT, status TEXT, duration_ms INTEGER)",
        ):
            conn.execute(sa.text(sql))

    def factory():
        return SQLiteSession(engine)

    clock = Clock()
    try:
        with patch(f"{MOD}.SessionLocal", factory), patch(f"{MOD}.utcnow", new=clock):
            factory.clock = clock
            yield factory
    finally:
        engine.dispose()


def bot():
    return SimpleNamespace(
        config=SimpleNamespace(guild=10, composio_entity="dobby", timezone="UTC"),
        toolset=object(),
        member_allowed=AsyncMock(return_value=True),
        ask_calendar_approval=AsyncMock(return_value=True),
    )


async def draft(factory, client, people=None, summary="Review", channel="40", requester="1"):
    people = people or [{"name": "Leonard", "discord_id": "2"}]
    async with factory() as session:
        ctx = RunContext(session, "10", channel, requester, client.toolset, client.config)
        ctx.known_discord_ids.update(p["discord_id"] for p in people)
        params = {
            "summary": summary,
            "start_datetime": "2099-10-12T10:00:00",
            "attendees": ["existing@example.com"],
            "deferred_invitees": people,
        }
        result = await prepare_calendar_action(ctx, CREATE, params)
        assert result["queued"]
        assert "**Waiting for email:**" in ctx.pending[0].preview
        assert "without waiting" in ctx.pending[0].preview
        return ctx.pending[0]


async def confirm_event(action, event_id="event123"):
    """What a 🟢 on the meeting proposal does; the calendar creates the meeting under `event_id`."""
    created = {"success": True, "data": {"response_data": {**EVENT, "id": event_id}}}
    with patch(f"{PROPOSALS}.run_action", new=AsyncMock(return_value=created)):
        return await action.execute()


async def save(factory, name="Leonard", discord_id="2", email="leonard@example.com"):
    async with factory() as session:
        await save_calendar_email(session, discord_id, email, name)
        await session.commit()


async def sql(factory, statement, **params):
    async with factory() as session:
        await session.execute(sa.text(statement), params)
        await session.commit()


async def invite_rows(factory):
    async with factory() as session:
        rows = await session.execute(
            sa.text("SELECT display_name, status, attempts FROM calendar_invites ORDER BY id")
        )
        return [tuple(r) for r in rows]


async def statuses(factory):
    return [status for _, status, _ in await invite_rows(factory)]


def prompts(client):
    """Every approval the bot posted: [(channel, requester, [actions])]."""
    return [call.args for call in client.ask_calendar_approval.await_args_list]


def meetings(*event_ids):
    """A calendar that returns the named meetings by ID and accepts anything else."""

    async def respond(toolset, name, params, entity):
        if name == GET:
            return {"success": True, "data": {"response_data": {**EVENT, "id": params["event_id"]}}}
        return {"success": True}

    assert event_ids  # documents which meetings the test expects to be looked up
    return respond


async def reconcile(client, *calendar_replies, respond=None):
    """One reconcile pass with the calendar answering each Composio call in order (or via `respond`)."""
    fake = AsyncMock(side_effect=respond) if respond else AsyncMock(side_effect=list(calendar_replies))
    with patch(f"{MOD}.run_action", new=fake) as backend:
        asked = await reconcile_invites(client)
    return asked, backend


async def approve(client, action, *calendar_replies):
    with patch(f"{MOD}.run_action", new=AsyncMock(side_effect=list(calendar_replies))) as backend:
        result = await action.execute()
    return result, backend


async def waiting_and_ready(factory, client):
    """A confirmed meeting with Leonard (ID 2) waiting; his email then arrives. Returns the prompt."""
    await confirm_event(await draft(factory, client))
    await save(factory)
    asked, _ = await reconcile(client, GOT)
    assert asked == 1
    return prompts(client)[-1][2][0]


@pytest.mark.parametrize("email_first", [False, True])
def test_late_email_asks_the_requester_and_adds_the_guest_only_after_approval(email_first):
    async def run():
        async with database() as factory:
            client = bot()
            with patch(f"{PROPOSALS}.run_action", new=AsyncMock(return_value=GOT)) as create:
                action = await draft(factory, client)
                create.assert_not_awaited()
                if email_first:
                    await save(factory)
                result = await action.execute()
                assert result["success"] and result["deferred_invites"]
                assert "deferred_invitees" not in create.await_args.args[2]
                assert "ask you to approve inviting them" in result["message"]
            assert await invite_rows(factory) == [("Leonard", "pending", 0)]
            if not email_first:
                asked, backend = await reconcile(client)
                assert asked == 0
                backend.assert_not_awaited()
                client.ask_calendar_approval.assert_not_awaited()
                await save(factory)

            asked, backend = await reconcile(client, GOT)
            assert asked == 1
            assert [c.args[1] for c in backend.await_args_list] == [GET]  # looked, did not touch
            [(channel, requester, actions)] = prompts(client)
            assert (channel, requester, len(actions)) == (40, 1, 1)
            action = actions[0]
            assert action.label == "Invite Leonard"
            assert "**Guest:** Leonard (leonard@example.com)" in action.preview
            assert "**Meeting:** Review" in action.preview and "Oct 12, 2099" in action.preview
            assert "notify the 1 existing invitees" in action.preview
            assert await statuses(factory) == ["proposed"]

            # Asking again while the prompt is live neither re-asks nor edits anything.
            asked, backend = await reconcile(client)
            assert asked == 0 and len(prompts(client)) == 1
            backend.assert_not_awaited()

            result, backend = await approve(client, action, GOT, {"success": True})
            assert result["success"] and "**Invited:** Leonard (leonard@example.com)" in result["message"]
            assert [c.args[1] for c in backend.await_args_list] == [GET, PATCH]
            assert backend.await_args.args[2] == {
                "calendar_id": "primary",
                "event_id": "event123",
                "attendees": ["existing@example.com", "leonard@example.com"],
                "send_updates": "all",
            }
            assert await statuses(factory) == ["completed"]
            asked, backend = await reconcile(client)
            assert asked == 0
            backend.assert_not_awaited()

    asyncio.run(run())


def test_unconfirmed_and_failed_events_create_no_invites():
    async def run():
        async with database() as factory:
            client = bot()
            action = await draft(factory, client)
            await save(factory)
            asked, backend = await reconcile(client)
            assert asked == 0
            backend.assert_not_awaited()
            with patch(f"{PROPOSALS}.run_action", new=AsyncMock(return_value={"success": False})):
                assert not (await action.execute())["success"]
            assert await invite_rows(factory) == []

    asyncio.run(run())


def test_declining_means_no_edit_and_no_second_ask():
    async def run():
        async with database() as factory:
            client = bot()
            action = await waiting_and_ready(factory, client)
            await action.release("cancelled")
            assert await statuses(factory) == ["declined"]
            asked, backend = await reconcile(client)
            assert asked == 0
            backend.assert_not_awaited()
            # A stray late confirmation of that prompt still cannot touch the calendar.
            result, backend = await approve(client, action)
            assert not result["success"] and "no longer waiting" in result["message"]
            backend.assert_not_awaited()

    asyncio.run(run())


def test_an_unanswered_prompt_is_asked_again_later_and_then_given_up():
    async def run():
        async with database() as factory:
            client = bot()
            action = await waiting_and_ready(factory, client)
            for attempt in range(1, MAX_TRIES):
                await action.release("expired")
                assert await invite_rows(factory) == [("Leonard", "pending", attempt)]
                asked, backend = await reconcile(client)
                assert asked == 0  # too soon: no nagging
                backend.assert_not_awaited()
                factory.clock.advance(RETRY_AFTER + timedelta(seconds=1))
                asked, _ = await reconcile(client, GOT)
                assert asked == 1
                action = prompts(client)[-1][2][0]
            assert len(prompts(client)) == MAX_TRIES
            await action.release("expired")
            factory.clock.advance(RETRY_AFTER + timedelta(seconds=1))
            asked, backend = await reconcile(client)
            assert asked == 0
            backend.assert_not_awaited()
            assert await invite_rows(factory) == [("Leonard", "expired", MAX_TRIES)]

    asyncio.run(run())


def test_a_prompt_that_lost_its_message_comes_back_after_the_cooldown():
    async def run():
        async with database() as factory:
            client = bot()
            await waiting_and_ready(factory, client)  # the bot "restarts": nothing releases this prompt
            asked, _ = await reconcile(client)
            assert asked == 0 and await statuses(factory) == ["proposed"]
            factory.clock.advance(STALE_AFTER + timedelta(seconds=1))
            asked, _ = await reconcile(client)
            assert asked == 0 and await statuses(factory) == ["pending"]  # reopened, still cooling down
            factory.clock.advance(RETRY_AFTER)
            asked, _ = await reconcile(client, GOT)
            assert asked == 1 and len(prompts(client)) == 2

    asyncio.run(run())


def test_a_slow_calendar_does_not_make_a_live_prompt_look_stale():
    async def run():
        async with database() as factory:
            client = bot()
            await confirm_event(await draft(factory, client))
            await save(factory)

            async def slow(toolset, name, params, entity):
                factory.clock.advance(timedelta(minutes=5))  # a slow provider, before the prompt is posted
                return GOT

            asked, _ = await reconcile(client, respond=slow)
            assert asked == 1
            action = prompts(client)[-1][2][0]
            # Still inside the prompt's own window, counted from when it was posted.
            factory.clock.advance(timedelta(seconds=APPROVAL_SECONDS - 60))
            await reconcile(client)
            assert await statuses(factory) == ["proposed"]
            result, _ = await approve(client, action, GOT, {"success": True})
            assert result["success"] and await statuses(factory) == ["completed"]

    asyncio.run(run())


def test_the_approval_window_outlasts_the_response_text():
    assert APPROVAL_SECONDS == 15 * 60
    assert STALE_AFTER > timedelta(seconds=APPROVAL_SECONDS)


def test_requester_who_lost_access_is_not_asked_and_the_calendar_is_not_touched():
    async def run():
        async with database() as factory:
            client = bot()
            await confirm_event(await draft(factory, client))
            await save(factory)
            client.member_allowed.return_value = False
            asked, backend = await reconcile(client)
            assert asked == 0
            backend.assert_not_awaited()
            client.member_allowed.assert_awaited_once_with(1, 40, mention=False)
            assert await invite_rows(factory) == [("Leonard", "pending", 1)]

    asyncio.run(run())


def test_people_still_waiting_cost_no_discord_or_calendar_calls():
    async def run():
        async with database() as factory:
            client = bot()
            for i in range(3):
                await confirm_event(await draft(factory, client, summary=f"Meeting {i}"), f"event{i}")
            asked, backend = await reconcile(client)
            assert asked == 0
            backend.assert_not_awaited()
            client.member_allowed.assert_not_awaited()
            assert await statuses(factory) == ["pending"] * 3

    asyncio.run(run())


@pytest.mark.parametrize("case", ["already_invited", "past", "cancelled"])
def test_finished_meetings_and_present_guests_ask_nothing(case):
    async def run():
        async with database() as factory:
            client = bot()
            await confirm_event(await draft(factory, client))
            await save(factory)
            event = {**EVENT}
            if case == "already_invited":
                event["attendees"] = [{"email": "Leonard@example.com"}]
            elif case == "past":
                event["end"] = {"dateTime": "2000-01-01T10:00:00+00:00"}
            else:
                event["status"] = "cancelled"
            asked, backend = await reconcile(client, {"success": True, "data": event})
            assert asked == 0
            backend.assert_awaited_once()
            assert backend.await_args.args[1] == GET
            client.ask_calendar_approval.assert_not_awaited()
            assert await statuses(factory) == ["completed" if case == "already_invited" else "expired"]

    asyncio.run(run())


def test_a_calendar_that_cannot_find_the_meeting_is_retried_a_few_times_then_dropped():
    async def run():
        async with database() as factory:
            client = bot()
            await confirm_event(await draft(factory, client))
            await save(factory)
            for _ in range(MAX_TRIES):
                asked, _ = await reconcile(client, {"success": False})
                assert asked == 0
                factory.clock.advance(RETRY_AFTER + timedelta(seconds=1))
            await reconcile(client)
            assert await invite_rows(factory) == [("Leonard", "expired", MAX_TRIES)]
            client.ask_calendar_approval.assert_not_awaited()

    asyncio.run(run())


def test_old_invitations_expire_whatever_their_state():
    async def run():
        async with database() as factory:
            client = bot()
            await confirm_event(await draft(factory, client))
            await sql(factory, "UPDATE calendar_invites SET created_at = :old", old="2000-01-01 00:00:00")
            await reconcile(client)
            assert await statuses(factory) == ["expired"]
            assert KEEP_FOR >= timedelta(days=30)

    asyncio.run(run())


def test_the_invitation_belongs_to_one_discord_id_not_to_a_name():
    async def run():
        async with database() as factory:
            client = bot()
            await confirm_event(await draft(factory, client, [{"name": "Leonard", "discord_id": "2"}]))
            # Someone else takes the display name "Leonard" and registers their own email.
            await save(factory, "Leonard", "666", "mallory@example.test")
            asked, backend = await reconcile(client)
            assert asked == 0
            backend.assert_not_awaited()
            assert await statuses(factory) == ["pending"]
            # The person asked for registers under a different display name and is the one invited.
            await save(factory, "Leonard Park", "2", "leonard@example.com")
            asked, _ = await reconcile(client, GOT)
            assert asked == 1
            assert "leonard@example.com" in prompts(client)[-1][2][0].preview
            assert "mallory" not in prompts(client)[-1][2][0].preview

    asyncio.run(run())


def test_guests_with_the_same_name_are_separate_invitations():
    async def run():
        async with database() as factory:
            client = bot()
            people = [{"name": "Sam", "discord_id": "3"}, {"name": "Sam", "discord_id": "4"}]
            await confirm_event(await draft(factory, client, people))
            assert await invite_rows(factory) == [("Sam", "pending", 0), ("Sam", "pending", 0)]
            await save(factory, "Sam Lee", "4", "sam4@example.com")
            asked, _ = await reconcile(client, GOT)
            assert asked == 1 and "sam4@example.com" in prompts(client)[0][2][0].preview
            assert await statuses(factory) == ["pending", "proposed"]

    asyncio.run(run())


def test_invitations_from_one_requester_and_channel_share_one_prompt():
    async def run():
        async with database() as factory:
            client = bot()
            await confirm_event(await draft(factory, client, summary="One"), "event1")
            await confirm_event(await draft(factory, client, summary="Two"), "event2")
            await confirm_event(await draft(factory, client, summary="Elsewhere", channel="41"), "event3")
            await save(factory)
            asked, _ = await reconcile(client, respond=meetings("event1", "event2", "event3"))
            assert asked == 3
            assert sorted((channel, len(actions)) for channel, _, actions in prompts(client)) == [
                (40, 2),
                (41, 1),
            ]

    asyncio.run(run())


def test_a_prompt_that_cannot_be_posted_gives_the_invitation_back():
    async def run():
        async with database() as factory:
            client = bot()
            client.ask_calendar_approval.return_value = False
            await confirm_event(await draft(factory, client))
            await save(factory)
            asked, _ = await reconcile(client, GOT)
            assert asked == 0 and await statuses(factory) == ["pending"]
            client.ask_calendar_approval.side_effect = RuntimeError("channel gone")
            factory.clock.advance(RETRY_AFTER + timedelta(seconds=1))
            asked, _ = await reconcile(client, GOT)
            assert asked == 0 and await statuses(factory) == ["pending"]

    asyncio.run(run())


def test_one_failing_access_check_does_not_stop_other_requesters_being_asked():
    async def run():
        async with database() as factory:
            client = bot()
            await confirm_event(await draft(factory, client, summary="One", requester="1"), "event1")
            await confirm_event(
                await draft(factory, client, summary="Two", requester="5", channel="41"), "event2"
            )
            await save(factory)

            async def access(user, channel, mention):
                if user == 1:
                    raise RuntimeError("discord is down")
                return True

            client.member_allowed.side_effect = access
            asked, _ = await reconcile(client, respond=meetings("event1", "event2"))
            assert asked == 1
            assert [(channel, requester) for channel, requester, _ in prompts(client)] == [(41, 5)]
            assert sorted(await statuses(factory)) == ["pending", "proposed"]

    asyncio.run(run())


def test_a_release_that_fails_is_contained_and_the_stale_sweep_recovers_the_row():
    async def run():
        async with database() as factory:
            client = bot()
            client.ask_calendar_approval.return_value = False
            await confirm_event(await draft(factory, client))
            await save(factory)
            with patch(f"{MOD}.reopen", new=AsyncMock(side_effect=RuntimeError("db down"))):
                asked, _ = await reconcile(client, GOT)
            assert asked == 0 and await statuses(factory) == ["proposed"]
            factory.clock.advance(STALE_AFTER + timedelta(seconds=1))
            await reconcile(client)
            assert await statuses(factory) == ["pending"]

    asyncio.run(run())


def test_email_changed_after_the_preview_means_nobody_is_invited():
    async def run():
        async with database() as factory:
            client = bot()
            action = await waiting_and_ready(factory, client)
            async with factory() as session:
                await set_calendar_email(session, "2", "someone-else@example.com")
                await session.commit()
            result, backend = await approve(client, action)
            assert not result["success"] and "changed" in result["message"]
            backend.assert_not_awaited()
            assert await statuses(factory) == ["pending"]

    asyncio.run(run())


def test_a_changed_email_is_asked_about_again_at_once_without_burning_a_try():
    async def run():
        async with database() as factory:
            client = bot()
            action = await waiting_and_ready(factory, client)
            async with factory() as session:
                await set_calendar_email(session, "2", "new-address@example.com")
                await session.commit()
            result, _ = await approve(client, action)
            assert not result["success"]
            assert await invite_rows(factory) == [("Leonard", "pending", 0)]
            asked, _ = await reconcile(client, GOT)  # no cooldown: the requester did answer
            assert asked == 1 and len(prompts(client)) == 2
            assert "new-address@example.com" in prompts(client)[-1][2][0].preview
            assert await invite_rows(factory) == [("Leonard", "proposed", 1)]

    asyncio.run(run())


def test_an_exact_id_row_replaces_a_legacy_name_row_for_the_same_meeting():
    async def run():
        async with database() as factory:
            client = bot()
            await sql(
                factory,
                "INSERT INTO calendar_invites (guild_id, channel_id, requester_id, calendar_id, event_id, "
                "name_key, display_name) VALUES ('10', '40', '1', 'primary', 'event123', 'leonard', 'Leonard')",
            )
            await confirm_event(await draft(factory, client))  # same meeting, same person, now by ID
            assert await invite_rows(factory) == [("Leonard", "expired", 0), ("Leonard", "pending", 0)]
            await save(factory)
            asked, _ = await reconcile(client, GOT)
            assert asked == 1 and sum(len(a) for _, _, a in prompts(client)) == 1

    asyncio.run(run())


def test_approval_rechecks_the_meeting_before_editing():
    async def run():
        async with database() as factory:
            client = bot()
            action = await waiting_and_ready(factory, client)
            result, backend = await approve(
                client, action, {"success": True, "data": {**EVENT, "status": "cancelled"}}
            )
            assert not result["success"] and "over or cancelled" in result["message"]
            assert [c.args[1] for c in backend.await_args_list] == [GET]
            assert await statuses(factory) == ["expired"]

    asyncio.run(run())


@pytest.mark.parametrize("failure", ["patch_fails", "meeting_unreadable", "raises"])
def test_a_failed_approval_never_completes_and_is_retried_later(failure):
    async def run():
        async with database() as factory:
            client = bot()
            action = await waiting_and_ready(factory, client)
            replies = {
                "patch_fails": [GOT, {"success": False}],
                "meeting_unreadable": [{"success": False}],
                "raises": [RuntimeError("provider down")],
            }[failure]
            result, _ = await approve(client, action, *replies)
            assert result["success"] is False
            assert await statuses(factory) == ["pending"]

    asyncio.run(run())


def test_approving_when_the_guest_is_already_present_only_marks_it_done():
    async def run():
        async with database() as factory:
            client = bot()
            action = await waiting_and_ready(factory, client)
            present = {**EVENT, "attendees": [{"email": "LEONARD@example.com"}]}
            result, backend = await approve(client, action, {"success": True, "data": present})
            assert result["success"]
            assert [c.args[1] for c in backend.await_args_list] == [GET]
            assert await statuses(factory) == ["completed"]

    asyncio.run(run())


def test_confirming_the_same_person_again_revives_a_finished_invitation_but_not_a_live_one():
    async def run():
        async with database() as factory:
            client = bot()
            action = await waiting_and_ready(factory, client)
            await confirm_event(await draft(factory, client))  # re-requested while a prompt is live
            assert await invite_rows(factory) == [("Leonard", "proposed", 1)]
            await action.release("cancelled")
            assert await statuses(factory) == ["declined"]
            await confirm_event(await draft(factory, client))
            assert await invite_rows(factory) == [("Leonard", "pending", 0)]

    asyncio.run(run())


# --- who an invitation is for -----------------------------------------------------------


def test_resolution_by_discord_id_needs_a_valid_email():
    async def run():
        async with database() as factory:
            await save(factory, "Leonard Park", "2")
            await save(factory, "Leonard Chen", "3", "chen@example.com")
            await sql(factory, "UPDATE users SET calendar_email = 'not-an-email' WHERE discord_id = '3'")
            async with factory() as session:
                assert (await resolve_person(session, {"discord_id": "2"}))["calendar_email"] == (
                    "leonard@example.com"
                )
                assert await resolve_person(session, {"discord_id": "3"}) is None
                assert await resolve_person(session, {"discord_id": "404"}) is None

    asyncio.run(run())


def test_rows_saved_before_ids_were_required_resolve_only_unambiguous_names():
    async def run():
        async with database() as factory:
            await save(factory, "Leonard Park", "2")
            await save(factory, "Leonard Chen", "3", "chen@example.com")
            legacy = {"discord_id": None}
            async with factory() as session:
                assert await resolve_person(session, {**legacy, "name_key": "leonard"}) is None
                assert await resolve_person(session, {**legacy, "name_key": "leonrd park"}) is None
                assert (await resolve_person(session, {**legacy, "name_key": "leonard park"}))[
                    "calendar_email"
                ] == "leonard@example.com"
            # A display name set to the bare first name cannot beat the real person either.
            await save(factory, "Leonard", "666", "mallory@example.test")
            async with factory() as session:
                assert await resolve_person(session, {**legacy, "name_key": "leonard"}) is None

    asyncio.run(run())


def test_legacy_name_rows_still_need_the_requesters_approval():
    async def run():
        async with database() as factory:
            client = bot()
            await save(factory, "Leonard Park", "2")
            await sql(
                factory,
                "INSERT INTO calendar_invites (guild_id, channel_id, requester_id, calendar_id, event_id, "
                "name_key, display_name) VALUES ('10', '40', '1', 'primary', 'event123', 'leonard', 'Leonard')",
            )
            asked, backend = await reconcile(client, GOT)
            assert asked == 1
            assert [c.args[1] for c in backend.await_args_list] == [GET]
            assert "leonard@example.com" in prompts(client)[0][2][0].preview

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


def test_module_exposes_no_path_that_patches_a_guest_list_without_an_approval():
    """reconcile_invites only reads the calendar; PATCH lives solely inside an approval's execute()."""
    import inspect

    assert "GOOGLECALENDAR_PATCH_EVENT" not in inspect.getsource(invitations.reconcile_invites)
    assert "GOOGLECALENDAR_PATCH_EVENT" not in inspect.getsource(invitations.propose)
