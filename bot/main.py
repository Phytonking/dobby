import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timedelta
import io
import json
import logging
import os
import re
import sys
import time
from zoneinfo import ZoneInfo
import discord
from discord import app_commands
from .calendar import Calendar
from .chat import Concierge
from . import format as fmt
from .config import Config
from .contacts import Contacts, mask, parse_pairs, valid_email
from .models import ConfigError, UserError, event_label
from .planner import Planner
from .service import ChooseEvent, NeedContacts, NeedTitle, Scheduler
from .voice import say

log = logging.getLogger("scheduler")


def safe(text):
    return discord.utils.escape_mentions(discord.utils.escape_markdown(str(text)))


def plain_title(text):
    """Escaped like safe(), but keeps the single " | " of "<thread or channel> | <event>" readable."""
    return safe(text).replace("\\|", "|")


def describe_place(channel):
    """Channel and thread names give Gemini a topic when the request omits a title."""
    name = str(getattr(channel, "name", "") or "")[:100]
    if isinstance(channel, discord.Thread):
        parent = getattr(channel, "parent", None)
        return {"channel": str(getattr(parent, "name", "") or "")[:100], "thread": name}
    return {"channel": name, "thread": None}


def preview(proposal, zone, note=None):
    """Structured, bold-labelled preview. `note` is a trailing italic line (context used)."""
    old = proposal.existing or {}
    merged = {**old, **proposal.body}
    head = [say("preview_intro"), "", f"**{proposal.action.title()} meeting**"]
    if proposal.resolved_by_title:
        # Found by title rather than ID: the 🟢/🔴 step doubles as "is this the right one?"
        head.append(
            "_"
            + say(
                "confirm_event_match",
                title=plain_title(old.get("summary", "(untitled)")),
                start=safe(fmt.when(old.get("start", {}).get("dateTime"), None, zone)),
            )
            + "_"
        )
    rows = [
        ("Title", plain_title(merged.get("summary", "(untitled)"))),
        (
            "When",
            safe(
                fmt.when(merged.get("start", {}).get("dateTime"), merged.get("end", {}).get("dateTime"), zone)
            ),
        ),
    ]
    for key in ("location", "description"):
        if merged.get(key):
            rows.append((key.title(), safe(merged[key])))
    if proposal.attendees:
        rows.append(("Invitees", ", ".join(safe(a) for a in proposal.attendees)))
    body = [f"**{label}:** {value}" for label, value in rows]
    if proposal.action == "update":
        diff = fmt.changes(old, proposal.body, zone)
        body.append("**Changes:**")
        body += [f"• {plain_title(line)}" for line in diff] or ["• (nothing visible)"]
    if old.get("attendees") and proposal.action != "create":
        body.append(f"_Google will notify the {len(old['attendees'])} existing invitees._")
    tail = ["", say("preview_outro")]
    if note:
        tail.append(f"_{note}_")
    return "\n".join(head + body + tail)


CONFIRM, CANCEL = "\U0001f7e2", "\U0001f534"  # green circle, red circle
CONFIRM_SECONDS = 120


@dataclass
class PendingConfirmation:
    """A preview waiting for the requester's reaction. Lives in memory only."""

    owner: int
    proposal: object
    channel_id: int
    message_id: int
    guild_id: int | None
    mention: bool  # mention previews also enforce MENTION_CHANNEL_IDS on reauthorization
    expires: float
    used: bool = False


FOLLOWUP_SECONDS = 300


@dataclass
class Followup:
    """A question Dobby asked in the channel; the requester's reply resumes the request."""

    kind: str  # "emails" | "title" | "choose"
    prompt_id: int
    request: str
    history: list
    place: dict
    expires: float
    names: list = field(default_factory=list)  # emails still needed
    emails: dict = field(default_factory=dict)  # name -> email supplied so far
    candidates: list = field(default_factory=list)  # event IDs offered for "choose"


def chunks(text, size=1900):
    parts = []
    while len(text) > size:
        cut = text.rfind("\n", 0, size)
        cut = cut if cut > 0 else size
        parts.append(text[:cut])
        text = text[cut:].lstrip("\n")
    parts.append(text)
    return parts


class Bot(discord.Client):
    def __init__(self, config):
        intents = discord.Intents.none()
        intents.guilds = True
        # Mentions work in every visible channel unless MENTION_CHANNEL_IDS narrows them,
        # so message content is always required. Enable the intent in the developer portal.
        intents.guild_messages = True
        intents.message_content = True
        # Confirmations are reactions on Dobby's own preview message.
        intents.guild_reactions = True
        super().__init__(intents=intents, allowed_mentions=discord.AllowedMentions.none(), max_messages=None)
        self.config = config
        self.tree = app_commands.CommandTree(self)
        self.calendar = Calendar(config)
        self.planner = Planner(config)
        # Questions about the bot and off-topic chat live in bot/chat.py, not the planner.
        self.concierge = Concierge(config, self.planner.client)
        self.contacts = Contacts(config.contacts_file)
        self.scheduler = Scheduler(self.planner, self.calendar, config.timezone, self.contacts)
        # One worker serializes Calendar credential refreshes and conflict checks/writes.
        self.executor = ThreadPoolExecutor(max_workers=1)
        self.pending = 0
        self.cooldowns = {}
        self.confirmations = {}
        self.expiry_tasks = {}
        self.awaiting = {}  # (channel_id, user_id) -> Followup
        self.register_commands()

    def allowed(self, interaction):
        return self.config.allows(
            interaction.guild_id,
            interaction.user.id,
            [r.id for r in getattr(interaction.user, "roles", [])],
            interaction.channel_id,
        )

    async def gate(self, interaction):
        if not self.allowed(interaction):
            await interaction.response.send_message(say("not_authorized"), ephemeral=True)
            return False
        if not self.take_cooldown(interaction.user.id):
            await interaction.response.send_message(say("cooldown"), ephemeral=True)
            return False
        await interaction.response.defer(thinking=True)
        return True

    def take_cooldown(self, user_id):
        now = time.monotonic()
        self.cooldowns = {key: stamp for key, stamp in self.cooldowns.items() if now - stamp < 10}
        if user_id in self.cooldowns:
            return False
        self.cooldowns[user_id] = now
        return True

    async def member_allowed(self, user_id, channel_id, mention=True):
        # Fetch current roles before returning a preview or accepting a confirmation.
        def denied(reason, status=None):
            log.warning(
                "mention_access_denied reason=%s user=%s channel=%s http_status=%s",
                reason,
                user_id,
                channel_id,
                status,
            )
            return False

        if mention and not self.config.mentionable(channel_id):
            return denied("mention_channel_not_allowed")
        guild = self.get_guild(self.config.guild)
        if guild is None:
            return denied("guild_not_cached")
        try:
            member = await guild.fetch_member(user_id)
            channel = guild.get_channel_or_thread(channel_id)
            if channel is None:
                channel = await guild.fetch_channel(channel_id)
            if not channel.permissions_for(member).view_channel:
                return denied("requester_cannot_view_channel")
            # Private threads inherit parent permissions, so visibility alone is
            # insufficient when reauthorizing a thread confirmation.
            if isinstance(channel, discord.Thread) and channel.is_private():
                if not channel.permissions_for(member).manage_threads:
                    await channel.fetch_member(user_id)
            if self.config.channels and channel_id not in self.config.channels:
                return denied("channel_not_in_ALLOWED_CHANNEL_IDS")
            if not self.config.allows(guild.id, member.id, [r.id for r in member.roles], channel_id):
                return denied("user_or_roles_not_in_allowlist")
            return True
        except discord.HTTPException as exc:
            return denied("discord_lookup_failed", exc.status)

    def pending_followup(self, message):
        """The open question this message answers, if it replies to Dobby's prompt."""
        key = (message.channel.id, message.author.id)
        followup = self.awaiting.get(key)
        if followup is None:
            return None
        if time.monotonic() >= followup.expires:
            del self.awaiting[key]
            return None
        reference = getattr(message, "reference", None)
        if reference is not None and getattr(reference, "message_id", None) == followup.prompt_id:
            return followup
        return None

    async def on_message(self, message):
        if (
            message.author.bot
            or not message.guild
            or message.guild.id != self.config.guild
            or not self.config.mentionable(message.channel.id)
        ):
            return
        followup = self.pending_followup(message)
        mentioned = self.user in message.mentions
        if not mentioned and followup is None:
            return
        if not await self.member_allowed(message.author.id, message.channel.id):
            await self.mention_reply(message, say("not_authorized"))
            return
        # A reply to Dobby's own question continues the earlier request without a new cooldown.
        if followup is None and not self.take_cooldown(message.author.id):
            await self.mention_reply(message, say("cooldown"))
            return
        text = re.sub(rf"<@!?{self.user.id}>", "", message.content).strip()
        try:
            await self.route(message, text, followup)
        except Exception as exc:
            # A failure while answering Dobby's own question must reach the user, not the log only.
            self.awaiting.pop((message.channel.id, message.author.id), None)
            await self.mention_reply(message, self.error(exc))

    async def route(self, message, text, followup):
        if followup is not None and followup.kind == "emails":
            pairs = parse_pairs(text, followup.names)
            if not pairs:
                await self.mention_reply(message, say("contact_invalid_email"))
                return
            for name, email in pairs.items():
                followup.emails[name] = email
                followup.names.remove(name)
                try:
                    self.contacts.save(name, email, added_by=message.author.id)
                    saved = True
                except (OSError, ValueError) as exc:
                    # Keep the email for this request even when the memory file is unwritable.
                    log.warning("contact_save_failed type=%s path=%s", type(exc).__name__, self.contacts.path)
                    saved = False
                await self.mention_reply(
                    message, say("contact_saved" if saved else "contact_not_saved", name=name)
                )
            if followup.names:
                followup.prompt_id = (
                    await self.ask(message, "ask_email", names=", ".join(followup.names))
                ).id
                followup.expires = time.monotonic() + FOLLOWUP_SECONDS
                return
            self.awaiting.pop((message.channel.id, message.author.id), None)
            await self.handle_request(
                message, followup.request, followup.history, followup.place, emails=followup.emails
            )
            return
        if followup is not None and followup.kind == "title":
            if not text:
                await self.mention_reply(message, say("ask_title"))
                return
            self.awaiting.pop((message.channel.id, message.author.id), None)
            await self.handle_request(
                message, followup.request, followup.history, followup.place, title=text[:200]
            )
            return
        if followup is not None and followup.kind == "choose":
            picked = re.fullmatch(r"\s*(\d{1,2})\s*\.?\s*", text)
            index = int(picked.group(1)) if picked else 0
            if not 1 <= index <= len(followup.candidates):
                await self.mention_reply(message, say("choose_event_intro"))
                return
            self.awaiting.pop((message.channel.id, message.author.id), None)
            await self.handle_request(
                message,
                followup.request,
                followup.history,
                followup.place,
                event_id=followup.candidates[index - 1],
            )
            return
        self.awaiting.pop((message.channel.id, message.author.id), None)
        await self.handle_request(message, text, fresh=True)

    async def ask(self, message, key, **fields):
        """Post one of Dobby's questions in the channel and return the message to reply to."""
        return await message.reply(say(key, **fields), mention_author=False)

    async def handle_request(
        self, message, request, history=None, place=None, event_id=None, title=None, fresh=False, emails=None
    ):
        reply = None
        try:
            reply = await message.reply(say("working"), mention_author=False)
            if not request or len(request) > 2000:
                raise UserError("Mention me with a meeting request under 2,000 characters.")
            # Context is gathered before planning so Gemini can infer a title from the
            # discussion instead of asking. Bounded, same-channel, text only, never persisted.
            if history is None:
                history = await self.gather_history(message)
            if place is None:
                place = describe_place(message.channel)
            if fresh:
                # Not a calendar request? Answer in Dobby's voice and stop here.
                kind, answer = await self.converse(request, history, place)
                if answer is not None:
                    log.info("chat_reply kind=%s", kind)
                    await reply.edit(content=answer[:1900])
                    return
            if event_id is None:
                selected = re.search(r"\bevent_id:([a-zA-Z0-9_-]+)", request)
                event_id = selected.group(1) if selected else None
            key = (message.channel.id, message.author.id)
            try:
                proposal = await self.work(
                    self.scheduler.prepare, request, event_id, message.id, history, place, title, emails
                )
            except NeedTitle:
                await reply.edit(content=say("ask_title"))
                self.awaiting[key] = Followup(
                    "title", reply.id, request, history, place, time.monotonic() + FOLLOWUP_SECONDS
                )
                return
            except ChooseEvent as exc:
                rows = [say("choose_event_intro")] + [
                    f"{i}. {safe(c.get('summary', '(untitled)'))} | {safe(c.get('start', {}).get('dateTime', ''))}"
                    for i, c in enumerate(exc.candidates, 1)
                ]
                await reply.edit(content="\n".join(rows)[:1900])
                self.awaiting[key] = Followup(
                    "choose",
                    reply.id,
                    request,
                    history,
                    place,
                    time.monotonic() + FOLLOWUP_SECONDS,
                    candidates=[c["id"] for c in exc.candidates],
                )
                return
            except NeedContacts as exc:
                # Ask for the missing emails; the requester's reply resumes this request.
                await reply.edit(content=say("ask_email", names=", ".join(exc.names)))
                self.awaiting[key] = Followup(
                    kind="emails",
                    prompt_id=reply.id,
                    request=request,
                    history=history,
                    place=place,
                    expires=time.monotonic() + FOLLOWUP_SECONDS,
                    names=list(exc.names),
                    emails=dict(emails or {}),
                )
                return
            # Recheck access if membership changed during planning.
            if not await self.member_allowed(message.author.id, message.channel.id):
                raise UserError("Your calendar access is no longer authorized.")
            content = preview(proposal, self.config.timezone, say("context_used", count=len(history)))
            await self.present(reply, content, message.author.id, proposal, mention=True)
        except discord.Forbidden:
            log.warning("mention_reply_forbidden channel=%s", message.channel.id)
            text = say("permission_missing")
            if reply:
                try:
                    await reply.edit(content=text)
                except discord.HTTPException:
                    await self.mention_reply(message, text)
            else:
                await self.mention_reply(message, text)
        except Exception as exc:
            text = self.error(exc)
            if reply:
                try:
                    await reply.edit(content=text)
                except discord.HTTPException:
                    await self.mention_reply(message, text)
            else:
                await self.mention_reply(message, text)

    async def present(self, reply, content, owner, proposal, mention):
        """Edit Dobby's placeholder into the full inline preview, then arm 🟢/🔴 on it."""
        first, *rest = chunks(content + "\n" + say("confirm_instructions"))
        await reply.edit(content=first)
        for part in rest:
            await reply.channel.send(part)
        for emoji in (CONFIRM, CANCEL):
            await reply.add_reaction(emoji)
        entry = PendingConfirmation(
            owner=owner,
            proposal=proposal,
            channel_id=reply.channel.id,
            message_id=reply.id,
            guild_id=getattr(getattr(reply, "guild", None), "id", None),
            mention=mention,
            expires=time.monotonic() + CONFIRM_SECONDS,
        )
        self.confirmations[reply.id] = entry
        self.expiry_tasks[reply.id] = asyncio.get_running_loop().create_task(self.expire_after(reply.id))

    async def on_raw_reaction_add(self, payload):
        entry = self.confirmations.get(payload.message_id)
        if entry is None or (self.user and payload.user_id == self.user.id):
            return
        emoji = str(payload.emoji)
        if emoji not in (CONFIRM, CANCEL) or payload.user_id != entry.owner:
            return
        if entry.used or time.monotonic() >= entry.expires:
            return
        # Set before the first await: a second reaction cannot issue another write.
        entry.used = True
        if not await self.member_allowed(entry.owner, entry.channel_id, mention=entry.mention):
            text = say("confirm_unusable")
        elif emoji == CANCEL:
            text = say("cancelled")
        else:
            try:
                result = await self.work(self.calendar.apply, entry.proposal)
                text = say(
                    {"create": "created", "update": "updated", "delete": "deleted"}[entry.proposal.action]
                )
                # Name and date instead of the event ID: "<thread or channel> | <event>, <m>/<d>".
                event = {**(entry.proposal.existing or {}), **entry.proposal.body, **(result or {})}
                text += "\n\n**Event:** " + plain_title(event_label(event, self.config.timezone))
                if entry.proposal.action == "update":
                    diff = fmt.changes(entry.proposal.existing, entry.proposal.body, self.config.timezone)
                    rows = [f"• {plain_title(line)}" for line in diff] or ["• (no visible change)"]
                    text += "\n**Changed:**\n" + "\n".join(rows)
                log.info(
                    "calendar_operation action=%s user=%s guild=%s",
                    entry.proposal.action,
                    payload.user_id,
                    entry.guild_id,
                )
            except Exception as exc:
                text = self.error(exc) + " " + say("apply_failed_suffix")
        await self.finish(entry, text)

    async def finish(self, entry, text):
        self.confirmations.pop(entry.message_id, None)
        task = self.expiry_tasks.pop(entry.message_id, None)
        if task and task is not asyncio.current_task():
            task.cancel()
        try:
            channel = self.get_channel(entry.channel_id) or await self.fetch_channel(entry.channel_id)
            message = channel.get_partial_message(entry.message_id)
            await message.edit(content=text)
            try:
                await message.clear_reactions()
            except discord.HTTPException:
                pass  # Needs Manage Messages; the entry is already retired.
        except discord.HTTPException as exc:
            log.warning("confirmation_edit_failed status=%s", exc.status)

    async def expire_after(self, message_id):
        await asyncio.sleep(CONFIRM_SECONDS)
        entry = self.confirmations.get(message_id)
        if entry and not entry.used:
            entry.used = True
            await self.finish(entry, say("confirm_expired"))

    async def converse(self, request, history, place):
        return await asyncio.get_running_loop().run_in_executor(
            self.executor, self.concierge.respond, request, history, place
        )

    async def gather_history(self, message):
        """Recent same-channel text with author display names, oldest first.

        Members without Read Message History get no context rather than an error, so a
        complete request still works for them.
        """
        if not message.channel.permissions_for(message.author).read_message_history:
            return []
        history = []
        async for prior in message.channel.history(limit=self.config.context_limit, before=message):
            if not prior.author.bot and prior.content:
                history.append(
                    {
                        "author": str(getattr(prior.author, "display_name", prior.author))[:100],
                        "text": prior.content[:1500],
                        "at": prior.created_at.isoformat(),
                    }
                )
        history.reverse()
        return history

    async def mention_reply(self, message, text):
        # Reply in the originating channel or thread without pinging the requester.
        try:
            await message.reply(text, mention_author=False)
        except discord.HTTPException:
            pass

    async def work(self, function, *args):
        if self.pending >= 4:
            raise UserError(say("busy"))
        self.pending += 1
        try:
            return await asyncio.get_running_loop().run_in_executor(self.executor, function, *args)
        finally:
            self.pending -= 1

    @staticmethod
    def error(exc):
        # Never log exception bodies: SDK errors can contain prompts, URLs or credentials.
        log.warning("request_failed type=%s", type(exc).__name__)
        if isinstance(exc, UserError):
            return say("needs_help", question=safe(str(exc))[:1500])
        return say("generic_failure")

    def register_commands(self):
        @self.tree.error
        async def command_error(interaction, error):
            text = self.error(error)
            if interaction.response.is_done():
                await interaction.edit_original_response(content=text, view=None)
            else:
                await interaction.response.send_message(text, ephemeral=True)

        @self.tree.command(
            name="schedule", description="Create, change or delete a Google Calendar meeting with Gemini"
        )
        @app_commands.guild_only()
        @app_commands.describe(
            request="Describe one meeting operation with a date, time and duration",
            event_id="For changes/deletes, copy the exact ID from /events",
        )
        async def schedule(
            interaction: discord.Interaction,
            request: app_commands.Range[str, 1, 2000],
            event_id: str | None = None,
        ):
            if not await self.gate(interaction):
                return
            try:
                place = describe_place(interaction.channel)
                proposal = await self.work(
                    self.scheduler.prepare, request, event_id, interaction.id, None, place
                )
                reply = await interaction.original_response()
                await self.present(
                    reply,
                    preview(proposal, self.config.timezone),
                    interaction.user.id,
                    proposal,
                    mention=False,
                )
            except Exception as exc:
                await interaction.edit_original_response(content=self.error(exc))

        @self.tree.command(
            name="events", description="List upcoming events and their exact IDs in this channel"
        )
        @app_commands.guild_only()
        async def events(interaction: discord.Interaction, days: app_commands.Range[int, 1, 90] = 14):
            if not await self.gate(interaction):
                return
            try:
                now = datetime.now(ZoneInfo(self.config.timezone))
                items = await self.work(
                    self.calendar.list, now.isoformat(), (now + timedelta(days=days)).isoformat()
                )
                rows = [
                    {
                        "id": e["id"],
                        "title": e.get("summary", "(private/untitled)"),
                        "start": e.get("start"),
                        "end": e.get("end"),
                    }
                    for e in items
                ]
                lines = [f"{safe(e['title'])[:90]} | {safe(e['start'])} | ID: `{e['id']}`" for e in rows[:8]]
                content = (
                    say("events_found", count=len(rows), days=days) + "\n" + "\n".join(lines)
                    if rows
                    else say("events_none")
                )
                files = (
                    [discord.File(io.BytesIO(json.dumps(rows, indent=2).encode()), filename="events.json")]
                    if rows
                    else []
                )
                await interaction.edit_original_response(content=content[:1900], attachments=files)
            except Exception as exc:
                await interaction.edit_original_response(content=self.error(exc))

        @self.tree.command(
            name="contacts", description="Teach Dobby who to invite: add, list or remove emails"
        )
        @app_commands.guild_only()
        @app_commands.describe(
            action="add saves a name and email, list shows names, remove forgets one",
            name="The name people use in requests, e.g. Maya",
            email="Required for add",
        )
        @app_commands.choices(
            action=[
                app_commands.Choice(name="add", value="add"),
                app_commands.Choice(name="list", value="list"),
                app_commands.Choice(name="remove", value="remove"),
            ]
        )
        async def contacts(
            interaction: discord.Interaction,
            action: app_commands.Choice[str],
            name: app_commands.Range[str, 1, 100] | None = None,
            email: app_commands.Range[str, 3, 254] | None = None,
        ):
            if not self.allowed(interaction):
                await interaction.response.send_message(say("not_authorized"), ephemeral=True)
                return
            choice = action.value
            if choice == "list":
                rows = self.contacts.all()
                text = (
                    say("contacts_list_intro")
                    + "\n"
                    + "\n".join(f"{safe(n)}: {mask(e)}" for n, e in rows.items())
                    if rows
                    else say("contacts_empty")
                )
            elif not name:
                text = say("needs_help", question="Give Dobby the name to add or remove.")
            elif choice == "remove":
                text = say(
                    "contact_removed" if self.contacts.remove(name) else "contact_unknown", name=safe(name)
                )
            elif not email or not valid_email(email.strip().strip("<>")):
                text = say("contact_invalid_email")
            else:
                self.contacts.save(name, email, added_by=interaction.user.id)
                text = say("contact_saved", name=safe(name))
            await interaction.response.send_message(text[:1900], ephemeral=True)

        @self.tree.command(name="calendar_help", description="Show scheduling examples and privacy details")
        @app_commands.guild_only()
        async def help_command(interaction: discord.Interaction):
            if not await self.gate(interaction):
                return
            await interaction.edit_original_response(
                content=(
                    say("help_intro") + "\n\n"
                    f"Team timezone: {self.config.timezone}\n"
                    "`/schedule request:Create Project Sync on October 12, 2026 at 10am for 30 minutes`\n"
                    "`/events days:30` → copy an event ID\n"
                    "`/schedule request:Move this meeting to October 13 at 2pm event_id:…`\n"
                    "`/schedule request:Delete this meeting event_id:…`\n"
                    "`@Dobby set up a design review Friday at 2pm and invite Maya and Leonard`\n"
                    "`@Dobby delete the design review` → Dobby finds it by title and asks you to confirm\n"
                    "`/contacts action:add name:Maya email:maya@example.com` → Dobby remembers who to invite\n"
                    "Default duration: 1 hour. Mention Dobby in an enabled channel to schedule; "
                    "recent channel messages and the thread name help Dobby pick a title. Preview appears in this channel or thread. "
                    "Changes require your confirmation. Existing guests receive Google notifications. "
                    "Only single timed meetings are supported. Conflicts check the linked calendar only. "
                    "Your request and selected event details go to Gemini; free-tier data may improve Google products. "
                    "Calendar replies are visible to everyone who can see this channel or thread. "
                    "Only the requester can confirm or cancel their proposal."
                )
            )

    async def setup_hook(self):
        guild = discord.Object(id=self.config.guild)
        self.tree.copy_global_to(guild=guild)
        await self.tree.sync(guild=guild)

    async def on_ready(self):
        log.info(
            "bot_ready guild=%s model=%s structured_output=response_json_schema planner_attempts=3",
            self.config.guild,
            self.config.model,
        )

    async def on_error(self, event_method, *args, **kwargs):
        log.warning("discord_event_failed event=%s", event_method)

    async def close(self):
        for task in self.expiry_tasks.values():
            task.cancel()
        await super().close()
        self.executor.shutdown(wait=True)
        self.calendar.session.close()
        self.planner.client.close()


def check_token_file(config):
    """Fail early with an actionable message; never reveal the token contents."""
    path = config.token_file
    if os.path.isdir(path):
        raise ConfigError(
            f"{path} is a directory, not a file. Docker creates one when the host file is missing. "
            "Remove it, then run the account-linking step before starting the bot."
        )
    if not os.path.exists(path):
        raise ConfigError(f"Google token file not found at {path}. Run the account-linking step first.")
    try:
        with open(path, encoding="utf-8") as stream:
            saved = json.load(stream)
    except OSError, ValueError:
        raise ConfigError(f"Google token file at {path} is unreadable or not valid JSON.") from None
    absent = [k for k in ("refresh_token", "client_id", "client_secret") if not saved.get(k)]
    if absent:
        raise ConfigError(
            f"Google token file at {path} is missing: {', '.join(absent)}. Link the account again."
        )


def check_data_dir(config):
    """Contacts live here; a root-owned bind mount is the usual reason writes fail in Docker."""
    path = config.data_dir
    probe = os.path.join(path, ".write-test")
    try:
        os.makedirs(path, exist_ok=True)
        with open(probe, "w", encoding="utf-8") as stream:
            stream.write("ok")
        os.remove(probe)
    except OSError as exc:
        raise ConfigError(
            f"Data directory {path} is not writable ({type(exc).__name__}). Dobby stores contacts.json there. "
            "Create the folder before the first `docker compose up` and make it owned by DOBBY_UID/DOBBY_GID "
            "(on Pi: mkdir -p data && chown $(id -u):$(id -g) data), then recreate the container."
        ) from None


def main(argv=None):
    logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(name)s %(message)s")
    log.setLevel(logging.INFO)
    check_only = "--check" in (sys.argv[1:] if argv is None else argv)
    try:
        config = Config.load()
        check_token_file(config)
        check_data_dir(config)
    except ConfigError as exc:
        # Authored text with no credential values, so it is safe to show the operator.
        log.error("startup_failed: %s", exc)
        raise SystemExit(2) from None
    except Exception as exc:
        # Any other loader failure keeps the redacted form: its text may quote the file.
        log.error("startup_failed type=%s; check configuration and OAuth setup", type(exc).__name__)
        raise SystemExit(1) from None
    if check_only:
        log.info(
            "config_ok guild=%s timezone=%s model=%s mention_channels=%d data_dir=%s",
            config.guild,
            config.timezone,
            config.model,
            len(config.mention_channels),
            config.data_dir,
        )
        return
    try:
        bot = Bot(config)
        bot.run(config.token, log_handler=None)
    except Exception as exc:
        log.error("startup_failed type=%s; check configuration and OAuth setup", type(exc).__name__)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
