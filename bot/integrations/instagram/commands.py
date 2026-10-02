import logging

import discord
from discord import app_commands

from ...models import UserError
from ...voice import say
from .publish import MAX_CAPTION, draft_post, draft_story, format_posts, list_posts, story_from_post

log = logging.getLogger("scheduler")


def register(bot):
    group = app_commands.Group(name="instagram", description="Post to the group's Instagram", guild_only=True)

    async def fail(interaction, label, exc):
        if isinstance(exc, UserError):
            await interaction.edit_original_response(content=say("needs_help", question=str(exc))[:2000])
            return
        log.warning("%s_failed type=%s", label, type(exc).__name__)
        await interaction.edit_original_response(content=say("generic_failure"))

    @group.command(name="posts", description="List our recent posts, numbered")
    async def posts(interaction: discord.Interaction):
        if not await bot.gate(interaction):
            return
        try:
            rows = await list_posts(bot.toolset, bot.config, bot.config.composio_entity)
            await interaction.edit_original_response(content=format_posts(rows)[:2000])
        except Exception as exc:
            await fail(interaction, "instagram_posts", exc)

    @group.command(name="post", description="Preview a photo post, then Confirm to publish it")
    @app_commands.describe(image_url="Public https URL of the image", caption="The caption")
    async def post(
        interaction: discord.Interaction,
        image_url: app_commands.Range[str, 8, 2000],
        caption: app_commands.Range[str, 0, MAX_CAPTION] = "",
    ):
        if not await bot.gate(interaction):
            return
        try:
            if not image_url.startswith("http"):
                raise UserError("image_url must be a public https link.")
            pending = draft_post(bot.toolset, bot.config, bot.config.composio_entity, image_url, caption)
            await bot.confirm(interaction, pending)
        except Exception as exc:
            await fail(interaction, "instagram_post", exc)

    @group.command(
        name="story", description="Preview a story from one of our posts or an image, then Confirm"
    )
    @app_commands.describe(post="Post number from /instagram posts", image_url="Or a public https image URL")
    async def story(
        interaction: discord.Interaction,
        post: app_commands.Range[int, 1, 50] | None = None,
        image_url: app_commands.Range[str, 8, 2000] | None = None,
    ):
        if not await bot.gate(interaction):
            return
        try:
            if post is not None:
                pending = await story_from_post(bot.toolset, bot.config, bot.config.composio_entity, post)
            elif image_url and image_url.startswith("http"):
                pending = draft_story(bot.toolset, bot.config, bot.config.composio_entity, image_url)
            else:
                raise UserError("Give a post number or a public https image_url.")
            await bot.confirm(interaction, pending)
        except Exception as exc:
            await fail(interaction, "instagram_story", exc)

    bot.tree.add_command(group)
