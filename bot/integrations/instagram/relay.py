"""Relay incoming Instagram DMs into Discord threads, and answer them from there.

A background poller (on when INSTAGRAM_DM_CHANNEL_ID is set) lists recent conversations every
INSTAGRAM_DM_POLL_SECONDS. For each new inbound message it opens a thread in that channel, or
reuses the conversation's thread while it is still open, and posts the sender and text. A teammate
answers with `/dm reply` or by mentioning Dobby in the thread; the reply is previewed and sent only
after the requester's Confirm, and the thread is then closed. `/dm close` closes it without replying.

Only mapping and cursor metadata is stored (`instagram_dm_threads`, migration 006): conversation
ID, the other person's Instagram-scoped ID, the Discord thread, open/closed, and the newest message
ID/time seen. Message text is never written to Postgres. On startup, conversations Dobby has no
cursor for are only relayed from the moment the bot started, so an existing inbox does not flood
the channel; conversations it already tracks pick up where they left off.
"""

import asyncio
import logging
import time
from datetime import datetime, timezone

import discord
import sqlalchemy as sa

from bot.AIModels import FunctionDeclaration

from ...db import SessionLocal
from ...memory import record_action
from ...models import UserError
from ...voice import say
from ..base import LocalTool, RunContext
from . import dms

log = logging.getLogger("scheduler")  # the logger bot.main shows at INFO

THREAD_ARCHIVE_MINUTES = 10080  # a week; Dobby closes it sooner once someone answers
MAX_THREAD_NAME = 100


class RelayStore:
    """The SQL behind the relay. Every value written is an ID, a status or a timestamp."""

    async def conversation(self, session, guild_id: str, conversation_id: str) -> dict | None:
        result = await session.execute(
            sa.text("SELECT * FROM instagram_dm_threads WHERE guild_id = :g AND conversation_id = :c"),
            {"g": guild_id, "c": conversation_id},
        )
        row = result.mappings().first()
        return dict(row) if row else None

    async def by_thread(self, session, thread_id: str) -> dict | None:
        result = await session.execute(
            sa.text("SELECT * FROM instagram_dm_threads WHERE discord_thread_id = :t"), {"t": str(thread_id)}
        )
        row = result.mappings().first()
        return dict(row) if row else None

    async def save_cursor(self, session, guild_id, conversation_id, message_id, message_at) -> None:
        await session.execute(
            sa.text(
                "INSERT INTO instagram_dm_threads (guild_id, conversation_id, last_message_id, last_message_at) "
                "VALUES (:g, :c, :mid, :at) ON CONFLICT (guild_id, conversation_id) DO UPDATE SET "
                "last_message_id = :mid, last_message_at = COALESCE(:at, instagram_dm_threads.last_message_at), "
                "updated_at = now()"
            ),
            {"g": guild_id, "c": conversation_id, "mid": message_id, "at": message_at},
        )

    async def open_thread(self, session, guild_id, conversation_id, channel_id, thread_id, participant_id):
        await session.execute(
            sa.text(
                "UPDATE instagram_dm_threads SET status = 'open', discord_channel_id = :ch, "
                "discord_thread_id = :t, participant_id = COALESCE(:p, participant_id), updated_at = now() "
                "WHERE guild_id = :g AND conversation_id = :c"
            ),
            {
                "g": guild_id,
                "c": conversation_id,
                "ch": str(channel_id),
                "t": str(thread_id),
                "p": participant_id or None,
            },
        )

    async def close(self, session, thread_id) -> bool:
        result = await session.execute(
            sa.text(
                "UPDATE instagram_dm_threads SET status = 'closed', updated_at = now() "
                "WHERE discord_thread_id = :t AND status = 'open'"
            ),
            {"t": str(thread_id)},
        )
        return bool(result.rowcount)


STORE = RelayStore()


# ----------------------------------------------------------------------------- helpers


def relay_parent_id(config, channel) -> int | None:
    """The relay channel's ID when `channel` is a thread inside it, else None."""
    parent = getattr(config, "instagram_dm_channel", 0)
    if parent and getattr(channel, "parent_id", None) == parent:
        return parent
    return None


def new_messages(messages: list[dms.Message], last_id, floor) -> list[dms.Message]:
    """Messages after the cursor, oldest first. `messages` is newest first.

    Walk back until the last message Dobby saw. `floor` (the cursor's time, or the bot's start for
    a conversation never seen) stops the walk too, so a missing ID cannot replay a whole inbox.
    """
    fresh = []
    for message in messages:
        if last_id and message.id == last_id:
            break
        if floor is not None:
            if message.created is None:
                if not last_id:
                    continue
            elif message.created < floor or (message.created == floor and not last_id):
                break
        fresh.append(message)
    fresh.reverse()
    return fresh


def format_dm(message: dms.Message) -> str:
    who = f"@{message.sender_name}" if message.sender_name else "Someone"
    stamp = message.created.strftime(" · %Y-%m-%d %H:%M UTC") if message.created else ""
    body = message.text.strip()
    if body:
        quoted = "\n".join("> " + line for line in body.splitlines())
    else:
        quoted = "> *(an attachment, sticker or reaction — open Instagram to see it)*"
    return f"**{who}** on Instagram{stamp}\n{quoted}"[:2000]


HINT = (
    "Answer with `/dm reply text:…` or `@Dobby` here: Dobby previews it, sends it after you press "
    "Confirm, and closes this thread. `/dm close` closes it without replying."
)


async def archive(channel) -> None:
    try:
        await channel.edit(archived=True)
    except discord.HTTPException as exc:
        log.warning("instagram_dm_archive_failed status=%s", exc.status)


def close_after_reply(thread_id):
    """after_success hook for a confirmed reply: mark the conversation answered and archive its thread."""

    async def close(channel) -> None:
        async with SessionLocal() as session:
            await STORE.close(session, thread_id)
            await session.commit()
        if channel is not None and str(getattr(channel, "id", "")) == str(thread_id):
            await archive(channel)

    return close


async def draft_thread_reply(session, toolset, config, thread_id, text: str):
    """The confirm-gated reply for the open relay thread `thread_id`."""
    row = await STORE.by_thread(session, str(thread_id))
    if row is None:
        raise UserError("This is not an Instagram DM thread Dobby opened.")
    if row["status"] != "open":
        raise UserError("This Instagram DM thread is already closed; a new message will open a new one.")
    return dms.draft_reply(
        toolset,
        config,
        config.composio_entity,
        row["participant_id"],
        text,
        after_success=close_after_reply(thread_id),
    )


# ----------------------------------------------------------------------------- poller


class DmRelay:
    def __init__(self, bot, *, store: RelayStore = STORE, started_at: datetime | None = None):
        self.bot = bot
        self.config = bot.config
        self.store = store
        self.started_at = started_at or datetime.now(timezone.utc)
        self.interval = getattr(self.config, "instagram_dm_poll_seconds", 60)
        self.task: asyncio.Task | None = None
        self._lock = asyncio.Lock()

    def start(self) -> None:
        if self.task is None or self.task.done():
            self.task = asyncio.get_running_loop().create_task(self.run())

    def stop(self) -> None:
        if self.task is not None:
            self.task.cancel()

    async def run(self) -> None:
        await self.bot.wait_until_ready()
        log.info(
            "instagram_dm_relay_started channel=%s every=%ss", self.config.instagram_dm_channel, self.interval
        )
        while True:
            try:
                await self.poll_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning("instagram_dm_poll_failed type=%s", type(exc).__name__)
            await asyncio.sleep(self.interval)

    async def poll_once(self) -> int:
        """One pass over recent conversations; returns how many messages were relayed."""
        async with self._lock:
            entity = self.config.composio_entity
            try:
                conversations = await dms.list_conversations(self.bot.toolset, self.config, entity)
            except UserError as exc:
                log.warning("instagram_dm_list_failed reason=%s", str(exc)[:200])
                return 0
            relayed = 0
            guild_id = str(self.config.guild)
            async with SessionLocal() as session:
                for conversation in conversations:
                    try:
                        relayed += await self._conversation(session, guild_id, conversation)
                        await session.commit()
                    except Exception as exc:
                        # Leave the cursor where it was; the next poll retries this conversation.
                        await session.rollback()
                        log.warning("instagram_dm_conversation_failed type=%s", type(exc).__name__)
            return relayed

    def _ours(self, message: dms.Message) -> bool:
        return message.sender_id == str(self.config.instagram_user_id) or message.id in dms.RECENTLY_SENT

    async def _conversation(self, session, guild_id: str, conversation: dms.Conversation) -> int:
        row = await self.store.conversation(session, guild_id, conversation.id)
        floor = row["last_message_at"] if row else self.started_at
        last_id = row["last_message_id"] if row else None
        if conversation.updated is not None and floor is not None and conversation.updated <= floor:
            if row is None:  # an old conversation: remember where it stands, relay nothing
                await self.store.save_cursor(session, guild_id, conversation.id, None, conversation.updated)
            return 0
        messages = await dms.list_messages(self.bot.toolset, conversation.id, self.config.composio_entity)
        if not messages:
            return 0
        fresh = new_messages(messages, last_id, floor)
        inbound = [m for m in fresh if not self._ours(m)]
        newest = messages[0]
        await self.store.save_cursor(session, guild_id, conversation.id, newest.id, newest.created)
        if not inbound:
            return 0
        start = time.monotonic()
        thread = await self._thread_for(session, guild_id, conversation.id, row, inbound[-1])
        for message in inbound:
            await thread.send(format_dm(message))
        await record_action(
            session,
            discord_id=str(getattr(self.bot.user, "id", "")),
            guild_id=guild_id,
            channel_id=str(thread.id),
            tool="instagram_dm.relay",
            status="ok",
            duration_ms=int((time.monotonic() - start) * 1000),
        )
        log.info("instagram_dm_relayed count=%d thread=%s", len(inbound), thread.id)
        return len(inbound)

    async def _channel(self, channel_id: int):
        try:
            return self.bot.get_channel(channel_id) or await self.bot.fetch_channel(channel_id)
        except discord.NotFound:
            return None

    async def _thread_for(self, session, guild_id, conversation_id, row, latest: dms.Message):
        """The conversation's open thread, or a new one under a short notice in the relay channel."""
        if row and row["status"] == "open" and row["discord_thread_id"]:
            thread = await self._channel(int(row["discord_thread_id"]))
            if thread is not None:
                await self.store.open_thread(
                    session, guild_id, conversation_id, thread.parent_id, thread.id, latest.sender_id
                )
                return thread  # posting un-archives a thread Discord auto-archived
        channel = await self._channel(self.config.instagram_dm_channel)
        if channel is None:
            raise RuntimeError("INSTAGRAM_DM_CHANNEL_ID is not a channel Dobby can see")
        who = f"@{latest.sender_name}" if latest.sender_name else "someone"
        notice = await channel.send(f"New Instagram DM from **{who}**")
        thread = await notice.create_thread(
            name=f"Instagram DM: {who}"[:MAX_THREAD_NAME], auto_archive_duration=THREAD_ARCHIVE_MINUTES
        )
        await thread.send(HINT)
        await self.store.open_thread(
            session, guild_id, conversation_id, channel.id, thread.id, latest.sender_id
        )
        return thread


# ----------------------------------------------------------------------------- model tool


async def draft_reply_tool(ctx: RunContext, params: dict) -> dict:
    text = str(params.get("text", ""))
    try:
        pending = await draft_thread_reply(ctx.session, ctx.toolset, ctx.config, ctx.channel_id, text)
    except UserError as exc:
        return {"success": False, "error": str(exc)}
    return ctx.queue(pending)


DRAFT_DM_REPLY_TOOL = LocalTool(
    declaration=FunctionDeclaration(
        name="draft_instagram_dm_reply",
        description=(
            "Only inside an Instagram DM thread Dobby opened: queue a reply to that conversation for "
            "the requester to confirm. `text` is sent exactly as given, so pass the wording the "
            "requester asked for. Once sent, Dobby closes the thread."
        ),
        parameters={
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "The exact message to send back on Instagram"}
            },
            "required": ["text"],
        },
    ),
    handler=draft_reply_tool,
)


async def close_thread(bot, interaction) -> str:
    """`/dm close`: mark the thread's conversation closed and archive it; returns what to say."""
    start = time.monotonic()
    async with SessionLocal() as session:
        closed = await STORE.close(session, interaction.channel_id)
        await record_action(
            session,
            discord_id=str(interaction.user.id),
            guild_id=str(interaction.guild_id),
            channel_id=str(interaction.channel_id),
            tool="instagram_dm.close",
            status="ok" if closed else "error",
            duration_ms=int((time.monotonic() - start) * 1000),
        )
        await session.commit()
    if not closed:
        raise UserError("This is not an open Instagram DM thread Dobby opened.")
    return say("dm_closed")
