import logging

import discord
from discord import app_commands

from ...voice import say

log = logging.getLogger("scheduler")


def register(bot):
    group = app_commands.Group(name="notion", description="Search Notion or create a note", guild_only=True)

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

    @group.command(name="search", description="Find Notion pages matching a query")
    @app_commands.describe(query="Words to search for")
    async def search(interaction: discord.Interaction, query: app_commands.Range[str, 1, 200]):
        await run(
            interaction,
            f"Search Notion for: {query}. Show up to 8 matching pages as title, one-line summary and link.",
            "notion_search",
        )

    @group.command(name="note", description="Create a Notion page")
    @app_commands.describe(title="Page title", content="Page body (plain text)")
    async def note(
        interaction: discord.Interaction,
        title: app_commands.Range[str, 1, 200],
        content: app_commands.Range[str, 1, 1800],
    ):
        parent = bot.config.notion_parent_page_id
        where = (
            f"under the parent page with ID {parent}"
            if parent
            else "in the workspace (ask if a parent is required)"
        )
        await run(
            interaction,
            f"Create a Notion page titled '{title}' {where}. Body:\n{content}\nReply with the new page's link.",
            "notion_note",
        )

    bot.tree.add_command(group)
