"""Step definitions for tests/features/instagram_dm_relay.feature (mocked Composio and Discord).

The real Bot, DmRelay, /dm commands and ConfirmView run; Instagram is a fake behind
`dms.run_action`, Discord channels/threads are small fakes, and Postgres is an in-memory
RelayStore that records every value written so the privacy scenario can inspect it.
"""

import asyncio
from datetime import datetime
from itertools import count
from unittest.mock import AsyncMock, Mock, patch

import discord
import pytest

pytest_bdd = pytest.importorskip("pytest_bdd")  # dev dependency; the slim Docker test image skips this
given, when, then, parsers, scenarios = (
    pytest_bdd.given,
    pytest_bdd.when,
    pytest_bdd.then,
    pytest_bdd.parsers,
    pytest_bdd.scenarios,
)

from bot.config import Config  # noqa: E402
from bot.integrations import ToolRegistry  # noqa: E402
from bot.integrations.base import RunContext  # noqa: E402
from bot.integrations.discord.client import Bot  # noqa: E402
from bot.integrations.instagram import dms, relay  # noqa: E402

scenarios("features/instagram_dm_relay.feature")

OUR_ACCOUNT = "1784"
GUILD = 10
ALLOWED = frozenset({1, 2})  # teammate 3 is not on the allowlist


# ----------------------------------------------------------------------------- fakes


def graph_time(value: str) -> str:
    """'10:05' means that time on the test day; anything else is a full ISO timestamp."""
    if len(value) == 5:
        value = f"2026-11-22T{value}:00+00:00"
    return datetime.fromisoformat(value).strftime("%Y-%m-%dT%H:%M:%S+0000")


class FakeInstagram:
    """Conversations as the Graph API would list them, plus every message Dobby sends."""

    def __init__(self):
        self.conversations: dict[str, list[dict]] = {}
        self.people: dict[str, str] = {}  # username -> IGSID
        self.sent: list[dict] = []
        self.failing = False
        self._ids = count(1)

    def igsid(self, username):
        return self.people.setdefault(username, f"{9000 + len(self.people)}")

    def add(self, conversation_id, sender_id, username, text, when):
        self.conversations.setdefault(conversation_id, []).insert(
            0,  # newest first
            {
                "id": f"mid.{next(self._ids)}",
                "created_time": graph_time(when),
                "from": {"id": sender_id, "username": username},
                "message": text,
            },
        )

    async def run_action(self, toolset, action, params, entity_id):
        if self.failing:
            return {"success": False, "error": "Instagram is down"}
        if action == dms.LIST_CONVERSATIONS:
            assert params["platform"] == "instagram" and params["ig_user_id"] == OUR_ACCOUNT
            items = [
                {"id": cid, "updated_time": messages[0]["created_time"]}
                for cid, messages in self.conversations.items()
            ]
            return {"success": True, "data": {"data": items, "paging": {"cursors": {}}}}
        if action == dms.LIST_MESSAGES:
            messages = self.conversations[params["conversation_id"]][: params["limit"]]
            return {"success": True, "data": {"data": list(messages), "paging": {}}}
        if action == dms.SEND_TEXT:
            self.sent.append(dict(params))
            conversation = next(
                cid
                for cid, messages in self.conversations.items()
                if any(m["from"]["id"] == params["recipient_id"] for m in messages)
            )
            self.add(conversation, OUR_ACCOUNT, "ourgroup", params["text"], "11:00")
            return {"success": True, "data": {"message_id": self.conversations[conversation][0]["id"]}}
        raise AssertionError(f"unexpected action {action}")


class FakeThread:
    def __init__(self, thread_id, parent_id, name):
        self.id = thread_id
        self.parent_id = parent_id
        self.name = name
        self.posts: list[str] = []
        self.archived = False

    async def send(self, content):
        self.posts.append(content)

    async def edit(self, archived=None, **_):
        if archived is not None:
            self.archived = archived


class FakeChannel:
    def __init__(self, world, channel_id):
        self.world = world
        self.id = channel_id
        self.notices: list[str] = []

    async def send(self, content):
        self.notices.append(content)
        channel = self

        class Notice:
            async def create_thread(self, name, auto_archive_duration):
                thread = FakeThread(next(channel.world.thread_ids), channel.id, name)
                channel.world.threads.append(thread)
                channel.world.channels[thread.id] = thread
                return thread

        return Notice()


class FakeStore(relay.RelayStore):
    """In-memory instagram_dm_threads that remembers every value it was asked to write."""

    def __init__(self):
        self.rows: dict[tuple, dict] = {}
        self.written: list = []

    async def conversation(self, session, guild_id, conversation_id):
        row = self.rows.get((guild_id, conversation_id))
        return dict(row) if row else None

    async def by_thread(self, session, thread_id):
        for row in self.rows.values():
            if row["discord_thread_id"] == str(thread_id):
                return dict(row)
        return None

    async def save_cursor(self, session, guild_id, conversation_id, message_id, message_at):
        self.written.append((guild_id, conversation_id, message_id, message_at))
        row = self.rows.setdefault(
            (guild_id, conversation_id),
            {
                "guild_id": guild_id,
                "conversation_id": conversation_id,
                "participant_id": None,
                "discord_channel_id": None,
                "discord_thread_id": None,
                "status": "closed",
                "last_message_id": None,
                "last_message_at": None,
            },
        )
        row["last_message_id"] = message_id
        if message_at is not None:
            row["last_message_at"] = message_at

    async def open_thread(self, session, guild_id, conversation_id, channel_id, thread_id, participant_id):
        self.written.append((guild_id, conversation_id, channel_id, thread_id, participant_id))
        row = self.rows[(guild_id, conversation_id)]
        row.update(status="open", discord_channel_id=str(channel_id), discord_thread_id=str(thread_id))
        if participant_id:
            row["participant_id"] = participant_id

    async def close(self, session, thread_id):
        self.written.append((thread_id,))
        for row in self.rows.values():
            if row["discord_thread_id"] == str(thread_id) and row["status"] == "open":
                row["status"] = "closed"
                return True
        return False


def session_factory():
    manager = AsyncMock()
    manager.__aenter__ = AsyncMock(return_value=AsyncMock(commit=AsyncMock(), rollback=AsyncMock()))
    manager.__aexit__ = AsyncMock(return_value=None)
    return manager


def make_config(dm_channel):
    return Config(
        token="t",
        guild=GUILD,
        users=ALLOWED,
        roles=frozenset(),
        channels=frozenset(),
        gemini_key="",
        composio_key="k",
        model="m",
        timezone="UTC",
        instagram_user_id=OUR_ACCOUNT,
        instagram_dm_channel=dm_channel,
    )


class World:
    def __init__(self, loop):
        self.loop = loop
        self.instagram = FakeInstagram()
        self.store = FakeStore()
        self.audit: list[dict] = []
        self.threads: list[FakeThread] = []
        self.thread_ids = count(5000)
        self.channels: dict[int, object] = {}
        self.bot = None
        self.interactions: dict[int, Mock] = {}
        self.view = None
        self.clicks: dict[int, Mock] = {}
        self.tool_result = None
        self.ctx = None

    def run(self, coro):
        return self.loop.run_until_complete(coro)

    def build_bot(self, dm_channel):
        config = make_config(dm_channel)

        async def build():
            with (
                patch("bot.integrations.discord.client.get_toolset", return_value=Mock()),
                patch("bot.integrations.discord.client.build_registry", return_value=ToolRegistry(tools=[])),
                patch("bot.integrations.discord.client.Agent"),
            ):
                return Bot(config)

        bot = self.run(build())
        bot._connection = Mock()
        bot._connection.user = Mock(id=5)
        bot.get_channel = lambda channel_id: self.channels.get(channel_id)
        bot.fetch_channel = AsyncMock(side_effect=discord.NotFound(Mock(status=404), "unknown"))
        if bot.dm_relay is not None:
            bot.dm_relay.store = self.store
        self.channels[dm_channel or 900] = FakeChannel(self, dm_channel or 900)
        self.bot = bot

    def thread_for(self, conversation_id):
        row = self.store.rows.get((str(GUILD), conversation_id))
        assert row and row["discord_thread_id"], f"no thread for {conversation_id}"
        return self.channels[int(row["discord_thread_id"])]

    def interaction(self, user_id, channel):
        interaction = Mock(spec=discord.Interaction)
        interaction.user = Mock(id=user_id, roles=[])
        interaction.guild_id = GUILD
        interaction.channel = channel
        interaction.channel_id = channel.id
        interaction.response = Mock(
            send_message=AsyncMock(), defer=AsyncMock(), is_done=Mock(return_value=True)
        )
        interaction.edit_original_response = AsyncMock(return_value=Mock())
        self.interactions[user_id] = interaction
        return interaction

    def command(self, name):
        return self.bot.tree.get_command("dm").get_command(name).callback


@pytest.fixture
def world():
    loop = asyncio.new_event_loop()
    state = World(loop)

    async def record(session, **kwargs):
        state.audit.append(kwargs)

    with (
        patch.object(dms, "run_action", new=state.instagram.run_action),
        patch.object(relay, "STORE", state.store),
        patch.object(relay, "SessionLocal", side_effect=session_factory),
        patch("bot.integrations.instagram.commands.SessionLocal", side_effect=session_factory),
        patch("bot.integrations.discord.confirm.SessionLocal", side_effect=session_factory),
        patch.object(relay, "record_action", new=record),
        patch("bot.integrations.discord.confirm.record_action", new=record),
    ):
        dms.RECENTLY_SENT.clear()
        yield state
    if state.bot is not None and state.bot.dm_relay is not None:
        state.bot.dm_relay.stop()
    loop.close()


# ----------------------------------------------------------------------------- given


@given(parsers.parse("the Instagram DM relay posts to channel {channel:d}"))
def relay_enabled(world, channel):
    world.build_bot(channel)


@given(parsers.parse('Dobby started at "{stamp}"'))
def started_at(world, stamp):
    world.bot.dm_relay.started_at = datetime.fromisoformat(stamp)


@given("INSTAGRAM_DM_CHANNEL_ID is unset")
def relay_disabled(world):
    world.build_bot(0)


@given(parsers.parse('"{username}" sends "{text}" at "{stamp}" in conversation "{conversation}"'))
@when(parsers.parse('"{username}" sends "{text}" at "{stamp}" in conversation "{conversation}"'))
def someone_sends(world, username, text, stamp, conversation):
    world.instagram.add(conversation, world.instagram.igsid(username), username, text, stamp)


@when(parsers.parse('our account sends "{text}" at "{stamp}" in conversation "{conversation}"'))
def we_send(world, text, stamp, conversation):
    world.instagram.add(conversation, OUR_ACCOUNT, "ourgroup", text, stamp)


@given(parsers.parse('an open DM thread from "{username}" in conversation "{conversation}"'))
def open_thread(world, username, conversation):
    someone_sends(world, username, "Hello there!", "10:05", conversation)
    world.run(world.bot.dm_relay.poll_once())
    assert world.thread_for(conversation)


@given("Instagram fails to answer")
def instagram_fails(world):
    world.instagram.failing = True


# ----------------------------------------------------------------------------- when


@given("Dobby polls Instagram")
@when("Dobby polls Instagram")
def poll(world):
    world.relayed = world.run(world.bot.dm_relay.poll_once())


@when("Instagram recovers")
def instagram_recovers(world):
    world.instagram.failing = False


@when(parsers.parse('teammate {user:d} runs "/dm reply" with "{text}" in the thread for "{conversation}"'))
def run_reply(world, user, text, conversation):
    interaction = world.interaction(user, world.thread_for(conversation))
    world.run(world.command("reply")(interaction, text=text))
    if interaction.edit_original_response.await_args:
        world.view = interaction.edit_original_response.await_args.kwargs.get("view")


@when(parsers.parse('teammate {user:d} runs "/dm reply" with "{text}" in a thread of channel {channel:d}'))
def run_reply_in_any_thread(world, user, text, channel):
    thread = FakeThread(4242, channel, "some thread")
    world.run(world.command("reply")(world.interaction(user, thread), text=text))


@when(parsers.parse('teammate {user:d} runs "/dm close" in the thread for "{conversation}"'))
def run_close(world, user, conversation):
    world.run(world.command("close")(world.interaction(user, world.thread_for(conversation))))


def click(world, user, button):
    interaction = Mock()
    interaction.user.id = user
    interaction.channel = world.interactions[1].channel
    interaction.response.send_message = AsyncMock()
    interaction.response.edit_message = AsyncMock()
    interaction.edit_original_response = AsyncMock()
    world.clicks[user] = interaction
    world.run(getattr(world.view, button).callback(interaction))


@when(parsers.parse("teammate {user:d} presses Confirm"))
def press_confirm(world, user):
    click(world, user, "confirm")


@when(parsers.parse("teammate {user:d} presses Cancel"))
def press_cancel(world, user):
    click(world, user, "cancel")


@when(parsers.parse('the model drafts the reply "{text}" in the thread for "{conversation}"'))
def model_drafts_in_thread(world, text, conversation):
    world.ctx = RunContext(
        AsyncMock(),
        str(GUILD),
        str(world.thread_for(conversation).id),
        "1",
        world.bot.toolset,
        world.bot.config,
    )
    world.tool_result = world.run(relay.DRAFT_DM_REPLY_TOOL.handler(world.ctx, {"text": text}))


@when(parsers.parse('the model drafts the reply "{text}" outside any DM thread'))
def model_drafts_elsewhere(world, text):
    ctx = RunContext(AsyncMock(), str(GUILD), "900", "1", world.bot.toolset, world.bot.config)
    world.tool_result = world.run(relay.DRAFT_DM_REPLY_TOOL.handler(ctx, {"text": text}))


# ----------------------------------------------------------------------------- then


@then(parsers.parse('a thread named "{name}" is opened in channel {channel:d}'))
def thread_opened(world, name, channel):
    assert [t.name for t in world.threads] == [name]
    assert world.threads[0].parent_id == channel
    assert world.channels[channel].notices, "the relay channel should get a notice to open the thread from"


@then(parsers.parse("{n:d} thread has been opened"))
@then(parsers.parse("{n:d} threads have been opened"))
def threads_opened(world, n):
    assert len(world.threads) == n


@then(parsers.parse('the thread for "{conversation}" shows "{text}"'))
def thread_shows(world, conversation, text):
    assert any(text in post for post in world.thread_for(conversation).posts)


@then(parsers.parse('the thread for "{conversation}" shows "{text}" exactly once'))
def thread_shows_once(world, conversation, text):
    assert sum(text in post for post in world.thread_for(conversation).posts) == 1


@then(parsers.parse('the thread for "{conversation}" does not show "{text}"'))
def thread_does_not_show(world, conversation, text):
    assert not any(text in post for post in world.thread_for(conversation).posts)


@then(parsers.parse('Dobby previews "{text}" with Confirm and Cancel'))
def previews(world, text):
    content = world.interactions[1].edit_original_response.await_args.kwargs["content"]
    assert f"**Instagram DM reply**\n{text}" in content
    assert {child.label for child in world.view.children} == {"Confirm", "Cancel"}
    assert world.view.requester_id == 1


@then("nothing has been sent on Instagram")
def nothing_sent(world):
    assert world.instagram.sent == []


@then(parsers.parse("teammate {user:d} is told only the requester can confirm"))
def told_not_requester(world, user):
    reply = world.clicks[user].response.send_message.await_args
    assert "Only the person who asked" in reply.args[0] and reply.kwargs["ephemeral"] is True


@then(parsers.parse('Instagram received exactly "{text}" for "{username}"'))
def instagram_received(world, text, username):
    assert world.instagram.sent == [
        {"ig_user_id": OUR_ACCOUNT, "recipient_id": world.instagram.people[username], "text": text}
    ]


@then(parsers.parse('Dobby reports the "{label}" was published'))
def reports_published(world, label):
    final = world.clicks[1].edit_original_response.await_args.kwargs["content"]
    assert label in final and "could not" not in final


@then(parsers.parse('the thread for "{conversation}" is archived'))
def thread_archived(world, conversation):
    assert world.thread_for(conversation).archived is True


@then(parsers.parse('the thread for "{conversation}" is not archived'))
def thread_not_archived(world, conversation):
    assert world.thread_for(conversation).archived is False


@then(parsers.parse('conversation "{conversation}" is closed'))
def conversation_closed(world, conversation):
    assert world.store.rows[(str(GUILD), conversation)]["status"] == "closed"


@then(parsers.parse('conversation "{conversation}" is open'))
def conversation_open(world, conversation):
    assert world.store.rows[(str(GUILD), conversation)]["status"] == "open"


@then("Dobby says the thread is closed")
def says_closed(world):
    content = world.interactions[1].edit_original_response.await_args.kwargs["content"]
    assert content == relay.say("dm_closed")


@then(parsers.parse('the reply "{text}" is queued for confirmation'))
def reply_queued(world, text):
    assert world.tool_result["queued"] is True
    assert [p.preview for p in world.ctx.pending] == [text]
    assert world.ctx.pending[0].after_success is not None  # closes the thread once sent


@then("the model is told it can only reply inside a DM thread")
def model_refused(world):
    assert world.tool_result["success"] is False and "Instagram DM thread" in world.tool_result["error"]


@then(parsers.parse("teammate {user:d} is told they are not authorized"))
def told_not_authorized(world, user):
    reply = world.interactions[user].response.send_message.await_args
    assert "not authorized" in reply.args[0] and reply.kwargs["ephemeral"] is True
    world.interactions[user].edit_original_response.assert_not_awaited()


@then("Dobby does not run the DM relay")
def relay_not_running(world):
    assert world.bot.dm_relay is None


@then("the /dm command is not offered")
def dm_not_offered(world):
    assert "dm" not in {c.name for c in world.bot.tree.get_commands()}


@then(parsers.parse("teammate {user:d} is told to use it inside an Instagram DM thread"))
def told_wrong_place(world, user):
    reply = world.interactions[user].response.send_message.await_args
    assert "inside an Instagram DM thread" in reply.args[0] and reply.kwargs["ephemeral"] is True


@then("the background poller keeps running after an unexpected error")
def poller_survives(world):
    relay_ = world.bot.dm_relay
    sleeps = []

    async def fake_sleep(seconds):
        sleeps.append(seconds)
        if len(sleeps) == 2:
            raise asyncio.CancelledError

    poll_once = AsyncMock(side_effect=[RuntimeError("boom"), 0])
    with (
        patch.object(relay_, "poll_once", poll_once),
        patch.object(relay.asyncio, "sleep", fake_sleep),
        patch.object(world.bot, "wait_until_ready", AsyncMock()),
    ):
        with pytest.raises(asyncio.CancelledError):
            world.run(relay_.run())
    assert poll_once.await_count == 2 and sleeps == [relay_.interval, relay_.interval]


@then(parsers.parse('no stored value or audit record contains "{text}"'))
def nothing_persisted(world, text):
    assert world.instagram.sent, "the scenario should have sent a reply"
    stored = [str(value) for entry in world.store.written for value in entry]
    stored += [str(value) for row in world.store.rows.values() for value in row.values()]
    stored += [str(value) for record in world.audit for value in record.values()]
    assert stored and not any(text in value for value in stored)
