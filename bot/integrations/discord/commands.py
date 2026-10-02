"""General slash commands that belong to no single service: /email and /help."""

import logging

import discord
from discord import app_commands

from ...db import SessionLocal
from ...memory import find_user_by_discord_id, set_calendar_email, save_calendar_email
from ...models import mask, valid_email
from ...voice import say

log = logging.getLogger("scheduler")


def register(bot):
    @bot.tree.command(
        name="email", description="Set, show or remove the calendar email Dobby invites you with"
    )
    @app_commands.guild_only()
    @app_commands.describe(
        action="set saves your address, show displays it masked, remove clears it",
        email="Required for set",
    )
    @app_commands.choices(
        action=[
            app_commands.Choice(name="set", value="set"),
            app_commands.Choice(name="show", value="show"),
            app_commands.Choice(name="remove", value="remove"),
        ]
    )
    async def email_command(
        interaction: discord.Interaction,
        action: app_commands.Choice[str],
        email: app_commands.Range[str, 3, 254] | None = None,
    ):
        if not bot.allowed(interaction):
            await interaction.response.send_message(say("not_authorized"), ephemeral=True)
            return
        choice = action.value
        discord_id = str(interaction.user.id)
        saved = False
        try:
            async with SessionLocal() as session:
                if choice == "show":
                    user = await find_user_by_discord_id(session, discord_id)
                    if user is None:
                        text = say("email_none")
                    elif user["calendar_email"]:
                        text = say("email_shown", email=mask(user["calendar_email"]))
                    else:
                        text = say("email_none")
                elif choice == "remove":
                    changed = await set_calendar_email(session, discord_id, None)
                    await session.commit()
                    text = say("email_removed") if changed else say("email_none")
                else:
                    address = (email or "").strip().strip("<>").lower()
                    if not valid_email(address):
                        text = say("email_invalid")
                    else:
                        user = await find_user_by_discord_id(session, discord_id)
                        name = user["display_name"] if user else interaction.user.display_name
                        await save_calendar_email(session, discord_id, address, name)
                        await session.commit()
                        text = say("email_saved")
                        saved = True
        except Exception as exc:
            log.warning("email_failed type=%s", type(exc).__name__)
            text = say("generic_failure")
        await interaction.response.send_message(text[:1900], ephemeral=True)
        if saved:
            await bot.reconcile_calendar_invites()

    @bot.tree.command(name="help", description="What Dobby can do, with examples and privacy details")
    @app_commands.guild_only()
    async def help_command(interaction: discord.Interaction):
        if not await bot.gate(interaction):
            return
        sections = [say("help_intro"), f"Dobby keeps time in {bot.config.timezone}."]
        for integration in bot.integrations:
            if integration.help_lines:
                sections.append(f"**{integration.label}**\n" + "\n".join(integration.help_lines))
        sections.append(
            "**You**\n"
            "`/email action:set email:you@uw.edu` → the address Dobby invites you with\n"
            "Or say `@Dobby my name is Leonard and my email is me@example.com`. "
            "Missing emails never hold up a meeting; approved invitations are added when the address arrives.\n"
            "Mention `@Dobby` in an enabled channel and Dobby will listen, whatever words you "
            "use. Before anything goes to Instagram or LinkedIn, Dobby shows you a preview and "
            "waits for Confirm. Dobby would never post without asking!\n"
            f"Dobby reads the last {bot.config.context_limit} messages in this channel for "
            "context and stores none of them. Your request and that context go to this server's "
            "configured AI model. Ask an admin where it runs and which data policy applies."
        )
        await interaction.edit_original_response(content="\n\n".join(sections)[:2000])
