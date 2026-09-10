import asyncio
from concurrent.futures import ThreadPoolExecutor
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
from .config import Config
from .models import ConfigError, UserError
from .planner import Planner
from .service import Scheduler
from .voice import say

log = logging.getLogger("scheduler")


def safe(text):
    return discord.utils.escape_mentions(discord.utils.escape_markdown(str(text)))


def describe_place(channel):
    """Channel and thread names give Gemini a topic when the request omits a title."""
    name = str(getattr(channel, "name", "") or "")[:100]
    if isinstance(channel, discord.Thread):
        parent = getattr(channel, "parent", None)
        return {"channel": str(getattr(parent, "name", "") or "")[:100], "thread": name}
    return {"channel": name, "thread": None}


def preview(proposal):
    old = proposal.existing or {}
    merged = {**old, **proposal.body}
    lines = [
        say("preview_intro"),
        f"**{proposal.action.title()} meeting**",
        f"Title: {safe(merged.get('summary', '(untitled)'))}",
        f"Start: {safe(merged.get('start', {}).get('dateTime', ''))}",
        f"End: {safe(merged.get('end', {}).get('dateTime', ''))}",
    ]
    for key in ("description", "location"):
        if key in proposal.body:
            lines.append(f"{key.title()}: {safe(proposal.body[key])}")
    if proposal.action == "update":
        lines.append("Changed fields: " + ", ".join(proposal.body))
    if old.get("attendees"):
        lines.append(f"Google will notify the {len(old['attendees'])} existing invitees.")
    lines.append(say("preview_outro"))
    return "\n".join(lines)


class Confirmation(discord.ui.View):
    def __init__(self, bot, owner, proposal, origin_channel=None):
        super().__init__(timeout=120)
        self.bot, self.owner, self.proposal = bot, owner, proposal
        self.used = False
        self.expires = time.monotonic() + 120
        self.origin_channel = origin_channel

    async def on_error(self, interaction, error, item):
        log.warning("confirmation_failed type=%s", type(error).__name__)

    async def interaction_check(self, interaction):
        authorized = False
        if interaction.user.id == self.owner:
            if self.origin_channel is None:
                authorized = self.bot.allowed(interaction)
            else:
                await interaction.response.defer()
                authorized = await self.bot.member_allowed(self.owner, self.origin_channel)
        if not authorized or self.used or time.monotonic() >= self.expires:
            send = (
                interaction.followup.send
                if interaction.response.is_done()
                else interaction.response.send_message
            )
            await send(say("confirm_unusable"), ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Confirm calendar change", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction, button):
        # Set before the first await: repeated clicks cannot issue another write.
        if self.used:
            return
        self.used = True
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True)
        try:
            result = await self.bot.work(self.bot.calendar.apply, self.proposal)
            text = say({"create": "created", "update": "updated", "delete": "deleted"}[self.proposal.action])
            if result.get("id"):
                text += f" Event ID: `{result['id']}`"
            log.info(
                "calendar_operation action=%s user=%s guild=%s",
                self.proposal.action,
                interaction.user.id,
                interaction.guild_id,
            )
        except Exception as exc:
            text = self.bot.error(exc) + " " + say("apply_failed_suffix")
        await interaction.edit_original_response(content=text, attachments=[], view=None)
        self.stop()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction, button):
        self.used = True
        if interaction.response.is_done():
            await interaction.edit_original_response(content=say("cancelled"), attachments=[], view=None)
        else:
            await interaction.response.edit_message(content=say("cancelled"), attachments=[], view=None)
        self.stop()


class Bot(discord.Client):
    def __init__(self, config):
        intents = discord.Intents.none()
        intents.guilds = True
        # Mentions work in every visible channel unless MENTION_CHANNEL_IDS narrows them,
        # so message content is always required. Enable the intent in the developer portal.
        intents.guild_messages = True
        intents.message_content = True
        super().__init__(intents=intents, allowed_mentions=discord.AllowedMentions.none(), max_messages=None)
        self.config = config
        self.tree = app_commands.CommandTree(self)
        self.calendar = Calendar(config)
        self.planner = Planner(config)
        self.scheduler = Scheduler(self.planner, self.calendar, config.timezone)
        # One worker serializes Calendar credential refreshes and conflict checks/writes.
        self.executor = ThreadPoolExecutor(max_workers=1)
        self.pending = 0
        self.cooldowns = {}
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

    async def member_allowed(self, user_id, channel_id):
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

        if not self.config.mentionable(channel_id):
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

    async def on_message(self, message):
        if (
            message.author.bot
            or not message.guild
            or message.guild.id != self.config.guild
            or not self.config.mentionable(message.channel.id)
            or self.user not in message.mentions
        ):
            return
        if not await self.member_allowed(message.author.id, message.channel.id):
            await self.mention_reply(message, say("not_authorized"))
            return
        if not self.take_cooldown(message.author.id):
            await self.mention_reply(message, say("cooldown"))
            return
        reply = None
        try:
            reply = await message.reply(say("working"), mention_author=False)
            request = re.sub(rf"<@!?{self.user.id}>", "", message.content).strip()
            if not request or len(request) > 2000:
                raise UserError("Mention me with a meeting request under 2,000 characters.")
            # Context is gathered before planning so Gemini can infer a title from the
            # discussion instead of asking. Bounded, same-channel, text only, never persisted.
            history = await self.gather_history(message)
            place = describe_place(message.channel)
            selected = re.search(r"\bevent_id:([a-zA-Z0-9_-]+)", request)
            event_id = selected.group(1) if selected else None
            proposal = await self.work(self.scheduler.prepare, request, event_id, message.id, history, place)
            # Recheck access if membership changed during planning.
            if not await self.member_allowed(message.author.id, message.channel.id):
                raise UserError("Your calendar access is no longer authorized.")
            content = preview(proposal)
            content += "\n" + say("context_used", count=len(history))
            await reply.edit(
                content=content
                if len(content) <= 1900
                else "Dobby has attached the preview. Please check it before confirming!",
                attachments=[discord.File(io.BytesIO(content.encode()), filename="calendar-preview.txt")],
                view=Confirmation(self, message.author.id, proposal, message.channel.id),
            )
        except discord.Forbidden:
            log.warning("mention_reply_forbidden channel=%s", message.channel.id)
            text = say("permission_missing")
            if reply:
                try:
                    await reply.edit(content=text, attachments=[], view=None)
                except discord.HTTPException:
                    await self.mention_reply(message, text)
            else:
                await self.mention_reply(message, text)
        except Exception as exc:
            text = self.error(exc)
            if reply:
                try:
                    await reply.edit(content=text, attachments=[], view=None)
                except discord.HTTPException:
                    await self.mention_reply(message, text)
            else:
                await self.mention_reply(message, text)

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
                proposal = await self.work(self.scheduler.prepare, request, event_id, interaction.id)
                content = preview(proposal)
                # Full preview is always reviewable even for unusually long existing titles.
                attachment = discord.File(io.BytesIO(content.encode()), filename="calendar-preview.txt")
                await interaction.edit_original_response(
                    content=content
                    if len(content) <= 1900
                    else "Dobby has attached the calendar preview. Please review it before confirming!",
                    attachments=[attachment],
                    view=Confirmation(self, interaction.user.id, proposal),
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


def main(argv=None):
    logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(name)s %(message)s")
    log.setLevel(logging.INFO)
    check_only = "--check" in (sys.argv[1:] if argv is None else argv)
    try:
        config = Config.load()
        check_token_file(config)
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
            "config_ok guild=%s timezone=%s model=%s mention_channels=%d",
            config.guild,
            config.timezone,
            config.model,
            len(config.mention_channels),
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
