import logging
import re
import sys
import time

import discord
from discord import app_commands
import sqlalchemy

from .agent import Agent
from .config import Config
from .contacts import mask, valid_email
from .db import SessionLocal
from .memory import load_guild_settings, save_contact, list_contacts, lookup_contact
from .models import ConfigError, UserError
from .voice import say

log = logging.getLogger("scheduler")


def safe(text):
    return discord.utils.escape_mentions(discord.utils.escape_markdown(str(text)))


class Bot(discord.Client):
    def __init__(self, config):
        intents = discord.Intents.none()
        intents.guilds = True
        # Message content intent needed for mention-based requests.
        intents.guild_messages = True
        intents.message_content = True
        super().__init__(intents=intents, allowed_mentions=discord.AllowedMentions.none(), max_messages=None)
        self.config = config
        self.tree = app_commands.CommandTree(self)
        self.agent = Agent(config)
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

    async def member_allowed(self, user_id, channel_id, mention=True):
        def denied(reason, status=None):
            log.warning(
                "mention_access_denied reason=%s user=%s channel=%s http_status=%s",
                reason, user_id, channel_id, status,
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

    async def on_message(self, message):
        if (
            message.author.bot
            or not message.guild
            or message.guild.id != self.config.guild
            or not self.config.mentionable(message.channel.id)
        ):
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
            async with SessionLocal() as session:
                settings = await load_guild_settings(session, str(message.guild.id))
                tz = settings["timezone"] if settings else self.config.timezone
                response = await self.agent.run(
                    session=session,
                    request=text,
                    guild_id=str(message.guild.id),
                    channel_id=str(message.channel.id),
                    discord_user_id=str(message.author.id),
                    entity_id=str(message.author.id),
                    timezone=tz,
                )
            await reply.edit(content=response[:1900])
        except Exception as exc:
            log.warning("mention_failed type=%s", type(exc).__name__)
            try:
                await reply.edit(content=say("generic_failure"))
            except discord.HTTPException:
                pass

    def register_commands(self):
        @self.tree.error
        async def command_error(interaction, error):
            text = say("generic_failure")
            if interaction.response.is_done():
                await interaction.edit_original_response(content=text, view=None)
            else:
                await interaction.response.send_message(text, ephemeral=True)

        @self.tree.command(
            name="schedule", description="Create, change or delete a Google Calendar meeting with Gemini"
        )
        @app_commands.guild_only()
        @app_commands.describe(request="Describe one meeting operation with a date, time and duration")
        async def schedule(
            interaction: discord.Interaction,
            request: app_commands.Range[str, 1, 2000],
        ):
            if not await self.gate(interaction):
                return
            try:
                async with SessionLocal() as session:
                    settings = await load_guild_settings(session, str(interaction.guild_id))
                    tz = settings["timezone"] if settings else self.config.timezone
                    response = await self.agent.run(
                        session=session,
                        request=request,
                        guild_id=str(interaction.guild_id),
                        channel_id=str(interaction.channel_id),
                        discord_user_id=str(interaction.user.id),
                        entity_id=str(interaction.user.id),
                        timezone=tz,
                    )
                await interaction.edit_original_response(content=response[:2000])
            except Exception as exc:
                log.warning("schedule_failed type=%s", type(exc).__name__)
                await interaction.edit_original_response(content=say("generic_failure"))

        @self.tree.command(
            name="events", description="List upcoming events in Google Calendar"
        )
        @app_commands.guild_only()
        async def events(interaction: discord.Interaction, days: app_commands.Range[int, 1, 90] = 14):
            if not await self.gate(interaction):
                return
            try:
                async with SessionLocal() as session:
                    settings = await load_guild_settings(session, str(interaction.guild_id))
                    tz = settings["timezone"] if settings else self.config.timezone
                    prompt = (
                        f"List my upcoming Google Calendar events for the next {days} days. "
                        "Show title, date/time, and event ID for each."
                    )
                    response = await self.agent.run(
                        session=session,
                        request=prompt,
                        guild_id=str(interaction.guild_id),
                        channel_id=str(interaction.channel_id),
                        discord_user_id=str(interaction.user.id),
                        entity_id=str(interaction.user.id),
                        timezone=tz,
                    )
                await interaction.edit_original_response(content=response[:2000])
            except Exception as exc:
                log.warning("events_failed type=%s", type(exc).__name__)
                await interaction.edit_original_response(content=say("generic_failure"))

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
            guild_id = str(interaction.guild_id)
            try:
                async with SessionLocal() as session:
                    if choice == "list":
                        rows = await list_contacts(session, guild_id)
                        text = (
                            say("contacts_list_intro")
                            + "\n"
                            + "\n".join(f"{safe(r['display_name'])}: {mask(r['email'])}" for r in rows)
                            if rows
                            else say("contacts_empty")
                        )
                    elif not name:
                        text = say("needs_help", question="Give Dobby the name to add or remove.")
                    elif choice == "remove":
                        existing = await lookup_contact(session, guild_id, name)
                        if existing:
                            await session.execute(
                                sqlalchemy.text(
                                    "DELETE FROM contacts WHERE guild_id = :g AND name_key = :nk"
                                ),
                                {"g": guild_id, "nk": " ".join(str(name).split()).lower()},
                            )
                            await session.commit()
                            text = say("contact_removed", name=safe(name))
                        else:
                            text = say("contact_unknown", name=safe(name))
                    elif not email or not valid_email(email.strip().strip("<>")):
                        text = say("contact_invalid_email")
                    else:
                        try:
                            await save_contact(
                                session, guild_id, name, email.strip().strip("<>"),
                                added_by=str(interaction.user.id),
                            )
                            await session.commit()
                            text = say("contact_saved", name=safe(name))
                        except Exception:
                            log.warning("contact_save_failed")
                            text = say("contact_not_saved", name=safe(name))
            except Exception as exc:
                log.warning("contacts_failed type=%s", type(exc).__name__)
                text = say("generic_failure")
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
                    "`/events days:30` → see upcoming events\n"
                    "`/schedule request:Move the design review to October 13 at 2pm`\n"
                    "`/schedule request:Delete the design review`\n"
                    "`@Dobby set up a design review Friday at 2pm and invite Maya and Leonard`\n"
                    "`/contacts action:add name:Maya email:maya@example.com` → Dobby remembers who to invite\n"
                    "Default duration: 1 hour. Mention Dobby in an enabled channel to schedule. "
                    "Dobby uses Composio to interact with Google Calendar, GitHub, and Notion. "
                    "Conversation history is remembered per channel. "
                    "Your request and conversation go to Gemini; free-tier data may improve Google products."
                )
            )

    async def setup_hook(self):
        guild = discord.Object(id=self.config.guild)
        self.tree.copy_global_to(guild=guild)
        await self.tree.sync(guild=guild)

    async def on_ready(self):
        log.info("bot_ready guild=%s model=%s", self.config.guild, self.config.model)

    async def on_error(self, event_method, *args, **kwargs):
        log.warning("discord_event_failed event=%s", event_method)

    async def close(self):
        await super().close()


def main(argv=None):
    logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(name)s %(message)s")
    log.setLevel(logging.INFO)
    check_only = "--check" in (sys.argv[1:] if argv is None else argv)
    try:
        config = Config.load()
    except ConfigError as exc:
        log.error("startup_failed: %s", exc)
        raise SystemExit(2) from None
    except Exception as exc:
        log.error("startup_failed type=%s; check configuration", type(exc).__name__)
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
        log.error("startup_failed type=%s; check configuration", type(exc).__name__)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
