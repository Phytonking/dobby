import logging

import discord
from discord import app_commands

from ...voice import say

log = logging.getLogger("scheduler")


def register(bot):
    @bot.tree.command(
        name="schedule", description="Create, change or delete a Google Calendar meeting with Dobby"
    )
    @app_commands.guild_only()
    @app_commands.describe(request="Describe one meeting operation with a date, time and duration")
    async def schedule(
        interaction: discord.Interaction,
        request: app_commands.Range[str, 1, 2000],
    ):
        if not await bot.gate(interaction):
            return
        try:
            result = await bot.ask_agent(
                request, interaction.guild_id, interaction.channel, interaction.user.id
            )
            await bot.send_result(interaction, result, interaction.user.id)
        except Exception as exc:
            log.warning("schedule_failed type=%s", type(exc).__name__)
            await interaction.edit_original_response(content=say("generic_failure"))

    @bot.tree.command(name="events", description="List upcoming events in Google Calendar")
    @app_commands.guild_only()
    async def events(interaction: discord.Interaction, days: app_commands.Range[int, 1, 90] = 14):
        if not await bot.gate(interaction):
            return
        try:
            prompt = (
                f"List the upcoming Google Calendar events for the next {days} days. "
                "Show title, date/time, and event ID for each."
            )
            result = await bot.ask_agent(
                prompt, interaction.guild_id, interaction.channel, interaction.user.id
            )
            await bot.send_result(interaction, result, interaction.user.id)
        except Exception as exc:
            log.warning("events_failed type=%s", type(exc).__name__)
            await interaction.edit_original_response(content=say("generic_failure"))
