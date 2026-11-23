import logging

import discord
from discord import app_commands

from ...voice import say

log = logging.getLogger("scheduler")


def register(bot):
    group = app_commands.Group(name="luma", description="Look up Luma events and guests", guild_only=True)

    async def run(interaction: discord.Interaction, prompt: str, label: str):
        if not await bot.gate(interaction):
            return
        try:
            result = await bot.ask_agent(
                prompt, interaction.guild_id, interaction.channel, interaction.user.id
            )
            await bot.send_result(interaction, result, interaction.user.id)
        except Exception as exc:
            log.warning("%s_failed type=%s", label, type(exc).__name__)
            await interaction.edit_original_response(content=say("generic_failure"))

    @group.command(name="events", description="List upcoming Luma events")
    async def events(interaction: discord.Interaction):
        await run(interaction, "List our upcoming Luma events as name, date and link.", "luma_events")

    @group.command(name="guests", description="Show who is registered for a Luma event")
    @app_commands.describe(event="Event name or evt- ID")
    async def guests(interaction: discord.Interaction, event: app_commands.Range[str, 1, 200]):
        await run(
            interaction,
            f"Find the Luma event '{event}' and summarise its guest list by approval status, naming approved guests.",
            "luma_guests",
        )

    bot.tree.add_command(group)
