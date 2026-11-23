import logging

import discord
from discord import app_commands

from ...voice import say

log = logging.getLogger("scheduler")


def register(bot):
    group = app_commands.Group(name="drive", description="Search or read Google Drive", guild_only=True)

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

    @group.command(name="search", description="Find Drive files matching a query")
    @app_commands.describe(query="Words from the file name or contents")
    async def search(interaction: discord.Interaction, query: app_commands.Range[str, 1, 200]):
        await run(
            interaction,
            f"Search Google Drive for: {query}. Show up to 8 files as name, type, last modified and link.",
            "drive_search",
        )

    @group.command(name="read", description="Summarise a Google Doc")
    @app_commands.describe(file="Document name, or a Drive link")
    async def read(interaction: discord.Interaction, file: app_commands.Range[str, 1, 300]):
        await run(
            interaction,
            f"Find the Google Doc matching: {file}. Read it and give a short summary with its link. "
            "If several match, list them and ask which one.",
            "drive_read",
        )

    bot.tree.add_command(group)
