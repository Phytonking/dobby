import logging
import re
import time

import discord
from discord import app_commands

from ...agent import Agent, AgentResult
from ...composio import get_toolset
from ...db import SessionLocal
from ...voice import say
from .. import INTEGRATIONS, build_registry
from . import commands as general_commands
from .confirm import ConfirmView, preview_text
from .calendar_confirm import present_calendar
from .context import gather_context, resolve_mentions
from .emails import capture_email, recover_recent_emails
from ..google_calendar.invitations import reconcile_invites

log = logging.getLogger("scheduler")


class Bot(discord.Client):
    def __init__(self, config, integrations=INTEGRATIONS):
        intents = discord.Intents.none()
        intents.guilds = True
        # Message content intent needed for mention-based requests.
        intents.guild_messages = True
        intents.guild_reactions = True
        intents.message_content = True
        super().__init__(intents=intents, allowed_mentions=discord.AllowedMentions.none(), max_messages=None)
        self.config = config
        self.integrations = integrations
        self.tree = app_commands.CommandTree(self)
        self.toolset = get_toolset(config.composio_key)
        self.agent = Agent(config, build_registry(self.toolset, integrations))
        self.agent.toolset = self.toolset
        self.cooldowns = {}
        self.calendar_confirmations = {}
        self.register_commands()

    # ------------------------------------------------------------------ access

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

    # ------------------------------------------------------------------ requests

    async def on_message(self, message):
        if (
            message.author.bot
            or not message.guild
            or message.guild.id != self.config.guild
            or not self.config.mentionable(message.channel.id)
        ):
            return
        if await capture_email(self, message):
            return
        if self.user not in message.mentions:
            return
        if not await self.member_allowed(message.author.id, message.channel.id):
            await message.reply(say("not_authorized"), mention_author=False)
            return
        if not self.take_cooldown(message.author.id):
            await message.reply(say("cooldown"), mention_author=False)
            return
        text = re.sub(rf"<@!?{self.user.id}>", "", message.content).strip()
        if not text or len(text) > 2000:
            await message.reply(
                say("needs_help", question="Mention me with a request under 2,000 characters."),
                mention_author=False,
            )
            return
        reply = await message.reply(say("working"), mention_author=False)
        try:
            result = await self.ask_agent(
                text, message.guild.id, message.channel, message.author.id, before=message
            )
            await self.send_result(reply, result, message.author.id)
        except Exception as exc:
            log.warning("mention_failed type=%s", type(exc).__name__)
            try:
                await reply.edit(content=say("generic_failure"))
            except discord.HTTPException:
                pass

    async def ask_agent(self, request, guild_id, channel, user_id, before=None) -> AgentResult:
        """Gather live channel context and mentioned people, then run the agent once."""
        await recover_recent_emails(self, channel, before=before)
        await self.reconcile_calendar_invites()
        context = await gather_context(channel, limit=self.config.context_limit, before=before)
        async with SessionLocal() as session:
            request, people = await resolve_mentions(session, request)
            return await self.agent.run(
                session=session,
                request=request,
                guild_id=str(guild_id),
                channel_id=str(channel.id),
                discord_user_id=str(user_id),
                context=context,
                known_people=people,
            )

    async def send_result(self, target, result: AgentResult, requester_id: int):
        """Show the agent's answer; attach Confirm/Cancel when something is waiting to be published.

        `target` is the placeholder `discord.Message` (mentions) or the `discord.Interaction`
        (slash commands) to edit.
        """
        if any(action.integration == "google_calendar" for action in result.pending):
            await present_calendar(self, target, result.pending, requester_id)
            return
        content = result.text.strip()
        view = None
        if result.pending:
            content = (content + "\n\n" if content else "") + preview_text(result.pending)
            guild_id = str(
                getattr(target, "guild_id", None) or getattr(getattr(target, "guild", None), "id", "")
            )
            channel_id = str(
                getattr(target, "channel_id", None) or getattr(getattr(target, "channel", None), "id", "")
            )
            view = ConfirmView(result.pending, requester_id, guild_id, channel_id)
        content = content[:2000]
        if isinstance(target, discord.Interaction):
            message = await target.edit_original_response(content=content, view=view)
        else:
            message = await target.edit(content=content, view=view)
        if view is not None:
            view.message = message

    async def confirm(self, interaction, pending, *, text: str = ""):
        """Used by commands that publish: show the preview and wait for the requester's buttons."""
        await self.send_result(interaction, AgentResult(text, [pending]), interaction.user.id)

    # ------------------------------------------------------------------ lifecycle

    async def reconcile_calendar_invites(self):
        try:
            return await reconcile_invites(self)
        except Exception as exc:
            log.warning("invite_reconciliation_failed type=%s", type(exc).__name__)
            return 0

    async def on_raw_reaction_add(self, payload):
        entry = self.calendar_confirmations.get(payload.message_id)
        if entry is not None and (self.user is None or payload.user_id != self.user.id):
            try:
                await entry.react(payload)
            except Exception as exc:
                log.warning("calendar_confirmation_failed type=%s", type(exc).__name__)
                await entry.finish(say("generic_failure"))

    async def close(self):
        for entry in self.calendar_confirmations.values():
            if entry.task:
                entry.task.cancel()
        self.calendar_confirmations.clear()
        await super().close()

    def register_commands(self):
        @self.tree.error
        async def command_error(interaction, error):
            log.warning("command_failed type=%s", type(error).__name__)
            text = say("generic_failure")
            if interaction.response.is_done():
                await interaction.edit_original_response(content=text, view=None)
            else:
                await interaction.response.send_message(text, ephemeral=True)

        general_commands.register(self)
        for integration in self.integrations:
            if integration.register_commands is not None:
                integration.register_commands(self)

    async def setup_hook(self):
        guild = discord.Object(id=self.config.guild)
        self.tree.copy_global_to(guild=guild)
        await self.tree.sync(guild=guild)

    async def on_ready(self):
        log.info("bot_ready guild=%s model=%s", self.config.guild, self.config.model)

    async def on_error(self, event_method, *args, **kwargs):
        log.warning("discord_event_failed event=%s", event_method)
