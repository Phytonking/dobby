import logging

import discord
from discord import app_commands

from ...voice import say
from .publish import MAX_CHARS, draft_post

log = logging.getLogger("scheduler")


def register(bot):
    group = app_commands.Group(
        name="linkedin", description="Publish to the group's LinkedIn", guild_only=True
    )

    @group.command(name="post", description="Preview a LinkedIn post, then Confirm to publish it")
    @app_commands.describe(text="The full post text")
    async def post(interaction: discord.Interaction, text: app_commands.Range[str, 1, MAX_CHARS]):
        if not await bot.gate(interaction):
            return
        try:
            pending = draft_post(bot.toolset, bot.config.composio_entity, text)
            await bot.confirm(interaction, pending)
        except Exception as exc:
            log.warning("linkedin_post_failed type=%s", type(exc).__name__)
            await interaction.edit_original_response(content=say("generic_failure"))

    bot.tree.add_command(group)
