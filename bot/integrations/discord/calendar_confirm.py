"""The original requester-only green/red reaction flow, backed by PendingActions."""

import asyncio
import logging
import time

import discord

from ...db import SessionLocal
from ...memory import record_action
from ...voice import say
from .confirm import TIMEOUT_SECONDS

log = logging.getLogger("confirm")
CONFIRM, CANCEL = "🟢", "🔴"


class CalendarConfirmation:
    def __init__(
        self,
        bot,
        message,
        pending,
        requester_id,
        *,
        mention,
        timeout=TIMEOUT_SECONDS,
        expired="confirm_expired",
    ):
        self.bot, self.message, self.pending = bot, message, pending
        self.requester_id, self.mention = requester_id, mention
        self.timeout = timeout
        self.expired = expired  # response key shown when nobody answers in time
        self.expires = time.monotonic() + timeout
        self.claimed = False
        self.task = None

    async def arm(self):
        # Register only after both controls have been added successfully.
        try:
            await self.message.add_reaction(CONFIRM)
            await self.message.add_reaction(CANCEL)
        except discord.HTTPException:
            await self.release("expired")
            await self.finish("Dobby could not add the confirmation reactions. No changes were made.")
            return
        self.bot.calendar_confirmations[self.message.id] = self
        self.task = asyncio.create_task(self.expire())

    async def expire(self):
        await asyncio.sleep(self.timeout)
        if not self.claimed:
            self.claimed = True
            await self.release("expired")
            await self.finish(say(self.expired))

    async def release(self, outcome):
        """Tell each action it will not run, so anything it reserved can be given back."""
        for action in self.pending:
            if action.release is None:
                continue
            try:
                await action.release(outcome)
            except Exception as exc:
                log.warning("release_failed type=%s", type(exc).__name__)

    async def react(self, payload):
        emoji = str(payload.emoji)
        if (
            payload.user_id != self.requester_id
            or emoji not in (CONFIRM, CANCEL)
            or payload.channel_id != self.message.channel.id
            or payload.guild_id != self.message.guild.id
            or self.claimed
            or time.monotonic() >= self.expires
        ):
            return
        self.claimed = True  # claim before any await; duplicate reactions cannot write twice
        if not await self.bot.member_allowed(
            self.requester_id, self.message.channel.id, mention=self.mention
        ):
            await self.release("expired")
            await self.finish(say("not_authorized"))
            return
        if emoji == CANCEL:
            await self.release("cancelled")
            await self.finish(say("calendar_cancelled"))
            return
        await self.message.edit(content=say("working"))
        lines = []
        for action in self.pending:
            start = time.monotonic()
            result = {}
            try:
                result = await action.execute()
                ok = bool(result.get("success"))
            except Exception as exc:
                log.warning("calendar_apply_failed type=%s", type(exc).__name__)
                ok = False
            try:
                async with SessionLocal() as session:
                    await record_action(
                        session,
                        discord_id=str(self.requester_id),
                        guild_id=str(self.message.guild.id),
                        channel_id=str(self.message.channel.id),
                        tool=f"{action.integration}.{action.label.split()[0].lower()}",
                        status="ok" if ok else "error",
                        duration_ms=int((time.monotonic() - start) * 1000),
                    )
                    await session.commit()
            except Exception as exc:
                log.warning("audit_failed type=%s", type(exc).__name__)
            if ok:
                lines.append(result.get("message") or say("published", label=action.label))
                if result.get("deferred_invites"):
                    asked = await self.bot.reconcile_calendar_invites()
                    if asked:
                        lines.append(
                            f"Dobby has {asked} waiting invitation(s) ready for your approval below!"
                        )
            else:
                lines.append(
                    result.get("message")
                    or f"Dobby could not complete **{action.label}**. Please check the calendar before trying again."
                )
        await self.finish("\n\n".join(lines))

    async def finish(self, content):
        self.bot.calendar_confirmations.pop(self.message.id, None)
        if self.task and self.task is not asyncio.current_task():
            self.task.cancel()
        try:
            await self.message.edit(content=content[:2000])
            await self.message.clear_reactions()
        except discord.HTTPException:
            pass


def split_message(content):
    """Discord's 2,000-character limit, split at line boundaries when possible."""
    chunks = []
    while len(content) > 2000:
        split = content.rfind("\n", 0, 2000)
        if split < 1:
            split = 2000
        chunks.append(content[:split])
        content = content[split:].lstrip("\n")
    chunks.append(content)
    return chunks


async def present_calendar(bot, target, pending, requester_id):
    content = say("calendar_preview_intro") + "\n\n"
    content += "\n\n".join(f"**{a.label}**\n{a.preview}" for a in pending)
    content += "\n\n" + say("calendar_preview_outro") + "\n" + say("calendar_confirm_instructions")
    # Do not attach approval to a truncated preview. Split at line boundaries when possible.
    chunks = split_message(content)
    slash = isinstance(target, discord.Interaction)
    if slash:
        message = await target.edit_original_response(content=chunks[0], view=None)
    else:
        message = await target.edit(content=chunks[0], view=None)
    for chunk in chunks[1:]:
        message = await message.channel.send(chunk)
    confirmation = CalendarConfirmation(bot, message, pending, requester_id, mention=not slash)
    await confirmation.arm()


async def present_invite_approval(bot, channel, pending, requester_id, timeout):
    """Ask the requester to approve late invitations in a new message; only their 🟢 adds the guests."""
    content = say("calendar_invite_intro", requester=f"<@{requester_id}>") + "\n\n"
    content += "\n\n".join(f"**{a.label}**\n{a.preview}" for a in pending)
    content += "\n\n" + say("calendar_invite_outro") + "\n" + say("calendar_confirm_instructions")
    chunks = split_message(content)
    # The client mentions nobody by default; ping only the requester, once, on the first message.
    ping = discord.AllowedMentions(
        everyone=False, roles=False, replied_user=False, users=[discord.Object(id=requester_id)]
    )
    message = await channel.send(chunks[0], allowed_mentions=ping)
    for chunk in chunks[1:]:
        message = await channel.send(chunk)
    confirmation = CalendarConfirmation(
        bot, message, pending, requester_id, mention=False, timeout=timeout, expired="calendar_invite_expired"
    )
    await confirmation.arm()
