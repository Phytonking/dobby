"""Late-invitation approvals against real Postgres.

Composio and Discord are fakes; the SQL (claims, upserts, timestamps), the advisory lock and the
name matching are production code. The SQLite unit tests cannot see dialect differences or races.
"""

import asyncio
import random
import uuid
from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bot.integrations.base import RunContext
from bot.integrations.discord.emails import capture_email
from bot.integrations.google_calendar import invitations as inv
from bot.integrations.google_calendar import proposals as prop
from bot.integrations.google_calendar.invitations import MAX_TRIES, RETRY_AFTER, STALE_AFTER

from .conftest import TEST_DATABASE_URL

CREATE, GET, PATCH = "GOOGLECALENDAR_CREATE_EVENT", "GOOGLECALENDAR_EVENTS_GET", "GOOGLECALENDAR_PATCH_EVENT"


def numeric_id():
    return str(uuid.uuid4().int % 10**17 + 10**17)


class FakeCalendar:
    """A stateful Google Calendar. `delay` makes reads slow, which widens any lost-update window."""

    def __init__(self, delay=0):
        self.events, self.calls, self.delay = {}, [], delay

    async def run(self, toolset, name, params, entity):
        self.calls.append((name, deepcopy(params)))
        if name == CREATE:
            event_id = f"evt{uuid.uuid4().hex[:10]}"
            self.events[event_id] = {
                "id": event_id,
                "summary": params["summary"],
                "start": {"dateTime": params["start_datetime"] + "+00:00"},
                "end": {"dateTime": params["end_datetime"] + "+00:00"},
                "attendees": [{"email": a} for a in params.get("attendees", [])],
            }
            return {"success": True, "data": {"response_data": deepcopy(self.events[event_id])}}
        if name == GET:
            snapshot = deepcopy(self.events[params["event_id"]])
            await asyncio.sleep(self.delay)
            return {"success": True, "data": {"response_data": snapshot}}
        if name == PATCH:
            if "attendees" in params:
                self.events[params["event_id"]]["attendees"] = [{"email": a} for a in params["attendees"]]
            return {"success": True, "data": {}}
        return {"success": False, "error": "unhandled " + name}

    def guests(self, event_id):
        return [a["email"] for a in self.events[event_id]["attendees"]]

    def patches(self):
        return [c for c in self.calls if c[0] == PATCH]


class Clock:
    def __init__(self):
        self.now = datetime.now(timezone.utc)

    def __call__(self):
        return self.now

    def advance(self, delta):
        self.now += delta


@asynccontextmanager
async def stack(**engine_options):
    """A fresh engine inside the running loop, wired into the modules that open their own sessions."""
    engine = create_async_engine(TEST_DATABASE_URL, **engine_options)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    guild = random.randint(10**9, 10**12)  # every test owns its guild: rows never collide
    calendar, clock = FakeCalendar(), Clock()
    bot = SimpleNamespace(
        config=SimpleNamespace(guild=guild, composio_entity="dobby", timezone="UTC"),
        toolset=object(),
        member_allowed=AsyncMock(return_value=True),
        ask_calendar_approval=AsyncMock(return_value=True),
        user=SimpleNamespace(id=99),
    )

    async def reconcile():
        return await inv.reconcile_invites(bot)

    bot.reconcile_calendar_invites = reconcile
    try:
        with (
            patch.object(inv, "SessionLocal", factory),
            patch("bot.integrations.discord.emails.SessionLocal", factory),
            patch.object(inv, "utcnow", new=clock),
            patch.object(inv, "run_action", new=calendar.run),
            patch.object(prop, "run_action", new=calendar.run),
        ):
            yield SimpleNamespace(factory=factory, guild=guild, calendar=calendar, clock=clock, bot=bot)
    finally:
        await engine.dispose()


async def add_user(env, name, email=None, discord_id=None):
    discord_id = discord_id or numeric_id()
    async with env.factory() as session:
        await session.execute(
            sa.text("INSERT INTO users (discord_id, display_name, calendar_email) VALUES (:d, :n, :e)"),
            {"d": discord_id, "n": name, "e": email},
        )
        await session.commit()
    return discord_id


async def make_meeting(env, people, *, requester=None, channel="40", summary="Design review"):
    """Propose a meeting waiting on `people`, confirm it, and return (event_id, proposal preview, requester)."""
    requester = requester or numeric_id()
    async with env.factory() as session:
        ctx = RunContext(session, str(env.guild), channel, requester, env.bot.toolset, env.bot.config)
        ctx.known_discord_ids.update(p["discord_id"] for p in people)
        result = await prop.prepare_calendar_action(
            ctx,
            CREATE,
            {
                "summary": summary,
                "start_datetime": "2099-10-12T10:00:00",
                "attendees": ["organizer@example.com"],
                "deferred_invitees": people,
            },
        )
    assert result["queued"], result
    # The agent's session is closed long before a 🟢 arrives; confirming must not rely on it.
    outcome = await ctx.pending[0].execute()
    assert outcome["success"] and outcome["deferred_invites"]
    return list(env.calendar.events)[-1], ctx.pending[0].preview, requester


async def rows(env):
    async with env.factory() as session:
        found = await session.execute(
            sa.text(
                "SELECT display_name, status, attempts FROM calendar_invites WHERE guild_id = :g ORDER BY created_at, id"
            ),
            {"g": str(env.guild)},
        )
        return [tuple(r) for r in found]


async def statuses(env):
    return [status for _, status, _ in await rows(env)]


def posted(env):
    """[(channel, requester, [actions])] for every approval prompt the bot posted."""
    return [call.args for call in env.bot.ask_calendar_approval.await_args_list]


def says(text, discord_id=None):
    author = SimpleNamespace(bot=False, id=int(discord_id or numeric_id()), display_name="someone")
    return SimpleNamespace(
        author=author,
        content=text,
        channel=SimpleNamespace(id=40),
        created_at=datetime.now(timezone.utc),
        reference=None,
        reply=AsyncMock(),
    )


def test_late_email_flow_on_real_postgres_adds_the_guest_only_after_approval(migrated_db):
    async def run():
        async with stack() as env:
            leonard = await add_user(env, "Leonard Park")  # registered, no email yet
            event_id, preview, requester = await make_meeting(
                env, [{"name": "Leonard", "discord_id": leonard}]
            )
            assert "**Invitees:** organizer@example.com" in preview
            assert f"**Waiting for email:** Leonard Park (<@{leonard}>)" in preview  # the registered name
            assert await rows(env) == [("Leonard", "pending", 0)]

            # Leonard shares his email in chat: Dobby asks, but does not touch the guest list.
            reply = says("my name is Leonard Park and my email is leonard@example.com", leonard)
            assert await capture_email(env.bot, reply)
            assert [r.args[0] for r in reply.reply.await_args_list][-1].startswith("Dobby has asked")
            assert env.calendar.guests(event_id) == ["organizer@example.com"]
            assert env.calendar.patches() == []
            [(channel, asked_requester, actions)] = posted(env)
            assert (channel, asked_requester) == (40, int(requester))
            assert "**Guest:** Leonard Park (leonard@example.com)" in actions[0].preview

            result = await actions[0].execute()  # the requester's 🟢
            assert result["success"]
            assert env.calendar.guests(event_id) == ["organizer@example.com", "leonard@example.com"]
            assert env.calendar.patches()[0][1]["send_updates"] == "all"
            assert await statuses(env) == ["completed"]
            assert await inv.reconcile_invites(env.bot) == 0

    asyncio.run(run())


def test_a_member_who_takes_the_display_name_cannot_capture_the_invitation(migrated_db):
    async def run():
        async with stack() as env:
            leonard = await add_user(env, "Leonard Park")
            event_id, _, _ = await make_meeting(env, [{"name": "Leonard", "discord_id": leonard}])
            mallory = numeric_id()
            await capture_email(
                env.bot, says("my name is Leonard and my email is mallory@example.test", mallory)
            )
            assert posted(env) == [] and env.calendar.patches() == []
            assert await statuses(env) == ["pending"]

            await capture_email(
                env.bot, says("my name is Leonard Park and my email is leonard@example.com", leonard)
            )
            [(_, _, actions)] = posted(env)
            assert "leonard@example.com" in actions[0].preview and "mallory" not in actions[0].preview
            assert env.calendar.guests(event_id) == ["organizer@example.com"]

    asyncio.run(run())


def test_simultaneous_reconciles_ask_exactly_once(migrated_db):
    async def run():
        async with stack() as env:
            leonard = await add_user(env, "Leonard Park", "leonard@example.com")
            await make_meeting(env, [{"name": "Leonard", "discord_id": leonard}])
            asked = await asyncio.gather(*(inv.reconcile_invites(env.bot) for _ in range(4)))
            assert sum(asked) == 1
            assert sum(len(actions) for _, _, actions in posted(env)) == 1
            assert await rows(env) == [("Leonard", "proposed", 1)]

    asyncio.run(run())


def test_two_approvals_for_one_meeting_are_serialized_so_both_guests_land(migrated_db):
    async def run():
        async with stack() as env:
            env.calendar.delay = 0.15  # a slow read: without the event lock the second PATCH drops a guest
            ana = await add_user(env, "Ana Reyes", "ana@example.com")
            ben = await add_user(env, "Ben Okoye", "ben@example.com")
            event_id, _, requester_a = await make_meeting(env, [{"name": "Ana", "discord_id": ana}])
            # A second requester, in another channel, also asked for a guest on the same meeting.
            async with env.factory() as session:
                other = RunContext(
                    session, str(env.guild), "41", numeric_id(), env.bot.toolset, env.bot.config
                )
                await inv.remember_invites(other, "primary", event_id, [{"name": "Ben", "discord_id": ben}])
            assert await inv.reconcile_invites(env.bot) == 2
            actions = [a for _, _, group in posted(env) for a in group]
            assert len(posted(env)) == 2 and len(actions) == 2
            results = await asyncio.gather(*(a.execute() for a in actions))
            assert all(r["success"] for r in results)
            assert sorted(env.calendar.guests(event_id)) == [
                "ana@example.com",
                "ben@example.com",
                "organizer@example.com",
            ]
            assert await statuses(env) == ["completed", "completed"]

    asyncio.run(run())


def test_an_approval_needs_only_the_connection_that_holds_the_event_lock(migrated_db):
    """A pool of one: if approving opened a second connection while holding the lock, it would hang."""

    async def run():
        async with stack(pool_size=1, max_overflow=0, pool_timeout=3) as env:
            leonard = await add_user(env, "Leonard Park", "leonard@example.com")
            event_id, _, _ = await make_meeting(env, [{"name": "Leonard", "discord_id": leonard}])
            assert await inv.reconcile_invites(env.bot) == 1
            result = await posted(env)[0][2][0].execute()
            assert result["success"], result
            assert env.calendar.guests(event_id) == ["organizer@example.com", "leonard@example.com"]
            assert await statuses(env) == ["completed"]

    asyncio.run(run())


def test_a_changed_email_is_asked_about_again_at_once(migrated_db):
    async def run():
        async with stack() as env:
            leonard = await add_user(env, "Leonard Park", "leonard@example.com")
            event_id, _, _ = await make_meeting(env, [{"name": "Leonard", "discord_id": leonard}])
            assert await inv.reconcile_invites(env.bot) == 1
            async with env.factory() as session:
                await session.execute(
                    sa.text("UPDATE users SET calendar_email = 'new@example.com' WHERE discord_id = :d"),
                    {"d": leonard},
                )
                await session.commit()
            result = await posted(env)[0][2][0].execute()
            assert not result["success"] and env.calendar.patches() == []
            assert await rows(env) == [("Leonard", "pending", 0)]
            assert await inv.reconcile_invites(env.bot) == 1  # no cooldown, no try burned
            assert "new@example.com" in posted(env)[1][2][0].preview
            assert env.calendar.guests(event_id) == ["organizer@example.com"]

    asyncio.run(run())


def test_cooldown_attempt_cap_and_stale_prompts_use_real_timestamps(migrated_db):
    async def run():
        async with stack() as env:
            leonard = await add_user(env, "Leonard Park", "leonard@example.com")
            await make_meeting(env, [{"name": "Leonard", "discord_id": leonard}])
            for attempt in range(1, MAX_TRIES + 1):
                assert await inv.reconcile_invites(env.bot) == 1
                action = posted(env)[-1][2][0]
                await action.release("expired")
                assert await rows(env) == [("Leonard", "pending", attempt)]
                assert await inv.reconcile_invites(env.bot) == 0  # inside the cooldown
                env.clock.advance(RETRY_AFTER + timedelta(seconds=1))
            assert await inv.reconcile_invites(env.bot) == 0
            assert await rows(env) == [("Leonard", "expired", MAX_TRIES)]
            assert len(posted(env)) == MAX_TRIES

    asyncio.run(run())


def test_a_prompt_lost_in_a_restart_is_reopened_not_stuck(migrated_db):
    async def run():
        async with stack() as env:
            leonard = await add_user(env, "Leonard Park", "leonard@example.com")
            await make_meeting(env, [{"name": "Leonard", "discord_id": leonard}])
            assert await inv.reconcile_invites(env.bot) == 1
            assert await statuses(env) == ["proposed"]
            env.clock.advance(STALE_AFTER + timedelta(seconds=1))
            assert await inv.reconcile_invites(env.bot) == 0
            assert await statuses(env) == ["pending"]
            env.clock.advance(RETRY_AFTER)
            assert await inv.reconcile_invites(env.bot) == 1

    asyncio.run(run())


def test_old_rows_expire_and_a_new_confirmed_request_revives_a_declined_one(migrated_db):
    async def run():
        async with stack() as env:
            leonard = await add_user(env, "Leonard Park", "leonard@example.com")
            people = [{"name": "Leonard", "discord_id": leonard}]
            await make_meeting(env, people)
            assert await inv.reconcile_invites(env.bot) == 1
            await posted(env)[-1][2][0].release("cancelled")
            assert await rows(env) == [("Leonard", "declined", 1)]

            # The same person on the same meeting, requested again and confirmed: back in the queue.
            event_id = list(env.calendar.events)[-1]
            async with env.factory() as session:
                ctx = RunContext(session, str(env.guild), "40", numeric_id(), env.bot.toolset, env.bot.config)
                await inv.remember_invites(ctx, "primary", event_id, people)
            assert await rows(env) == [("Leonard", "pending", 0)]

            async with env.factory() as session:
                await session.execute(
                    sa.text(
                        "UPDATE calendar_invites SET created_at = now() - interval '200 days' WHERE guild_id = :g"
                    ),
                    {"g": str(env.guild)},
                )
                await session.commit()
            assert await inv.reconcile_invites(env.bot) == 0
            assert await statuses(env) == ["expired"]

    asyncio.run(run())


def test_rows_saved_before_ids_were_required_resolve_by_unique_name_and_still_ask(migrated_db):
    async def run():
        async with stack() as env:
            tag = uuid.uuid4().hex[:6]
            event_id = "evt-legacy"
            env.calendar.events[event_id] = {
                "id": event_id,
                "summary": "Legacy",
                "start": {"dateTime": "2099-10-12T10:00:00+00:00"},
                "end": {"dateTime": "2099-10-12T11:00:00+00:00"},
                "attendees": [],
            }
            async with env.factory() as session:
                await session.execute(
                    sa.text(
                        "INSERT INTO calendar_invites (guild_id, channel_id, requester_id, calendar_id, event_id, "
                        "name_key, display_name) VALUES (:g, '40', :r, 'primary', :e, :k, :n)"
                    ),
                    {
                        "g": str(env.guild),
                        "r": numeric_id(),
                        "e": event_id,
                        "k": f"zed{tag}",
                        "n": f"Zed{tag}",
                    },
                )
                await session.commit()
            real = await add_user(env, f"Zed{tag} Quinn", f"zed{tag}@example.com")
            squatter = await add_user(env, f"Zed{tag}", f"squat{tag}@example.test")
            # "Zed" now names two people (one has set the bare first name as display name): no guess.
            assert await inv.reconcile_invites(env.bot) == 0
            async with env.factory() as session:
                await session.execute(sa.text("DELETE FROM users WHERE discord_id = :d"), {"d": squatter})
                await session.commit()
            assert await inv.reconcile_invites(env.bot) == 1
            assert f"zed{tag}@example.com" in posted(env)[0][2][0].preview
            assert env.calendar.patches() == []
            assert real  # the registered person is the one asked about

    asyncio.run(run())
