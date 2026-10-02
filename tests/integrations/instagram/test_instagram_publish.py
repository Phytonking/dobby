"""Tests for the Instagram package: listing, posts and stories as PendingActions, confirm-gated."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from bot.integrations.base import RunContext
from bot.integrations.instagram import INTEGRATION
from bot.integrations.instagram.publish import (
    CREATE_CONTAINER,
    DRAFT_POST_TOOL,
    DRAFT_STORY_TOOL,
    LIST_MEDIA,
    LIST_POSTS_TOOL,
    PUBLISH,
    draft_post,
    format_posts,
    list_posts,
    story_from_post,
)
from bot.models import UserError

MOD = "bot.integrations.instagram.publish"
CONFIG = SimpleNamespace(instagram_user_id="1784", composio_entity="dobby")
MEDIA = {
    "success": True,
    "data": {
        "data": [
            {
                "id": "m1",
                "caption": "Workshop night",
                "media_type": "IMAGE",
                "media_url": "https://cdn/1.jpg",
                "permalink": "https://ig/p/1",
            },
            {
                "id": "m2",
                "caption": None,
                "media_type": "VIDEO",
                "media_url": "https://cdn/2.mp4",
                "permalink": "https://ig/p/2",
            },
            {
                "id": "m3",
                "caption": "Carousel",
                "media_type": "CAROUSEL_ALBUM",
                "permalink": "https://ig/p/3",
            },
        ]
    },
}


def test_list_posts_numbers_newest_first_and_requires_account_id():
    async def run():
        with patch(f"{MOD}.run_action", new=AsyncMock(return_value=MEDIA)) as calls:
            posts = await list_posts("toolset", CONFIG, "dobby")
        assert calls.await_args.args[1] == LIST_MEDIA
        assert calls.await_args.args[2]["ig_user_id"] == "1784"
        assert [(p["number"], p["id"]) for p in posts] == [(1, "m1"), (2, "m2"), (3, "m3")]
        assert posts[2]["media_url"] is None
        assert "1. Workshop night — https://ig/p/1" in format_posts(posts)
        assert "2. (no caption)" in format_posts(posts)

        with pytest.raises(UserError, match="INSTAGRAM_USER_ID"):
            await list_posts("toolset", SimpleNamespace(instagram_user_id=""), "dobby")

        with patch(f"{MOD}.run_action", new=AsyncMock(return_value={"success": False, "error": "expired"})):
            with pytest.raises(UserError, match="expired"):
                await list_posts("toolset", CONFIG, "dobby")

    asyncio.run(run())


def test_draft_post_publishes_in_two_steps_only_on_execute():
    pending = draft_post("toolset", CONFIG, "dobby", "https://cdn/new.jpg", " Big news ")
    assert pending.label == "Instagram post" and pending.preview == "Big news\nhttps://cdn/new.jpg"

    async def run():
        calls = AsyncMock(
            side_effect=[
                {"success": True, "data": {"id": "container9"}},
                {"success": True, "data": {"id": "published9"}},
            ]
        )
        with patch(f"{MOD}.run_action", calls):
            result = await pending.execute()
        assert result["success"] is True
        first, second = calls.await_args_list
        assert first.args[1] == CREATE_CONTAINER
        assert first.args[2] == {
            "ig_user_id": "1784",
            "image_url": "https://cdn/new.jpg",
            "caption": "Big news",
        }
        assert second.args[1] == PUBLISH and second.args[2] == {
            "ig_user_id": "1784",
            "creation_id": "container9",
        }

    asyncio.run(run())


def test_publish_stops_if_the_container_fails_or_has_no_id():
    async def run():
        with patch(
            f"{MOD}.run_action", new=AsyncMock(return_value={"success": False, "error": "bad url"})
        ) as calls:
            result = await draft_post("toolset", CONFIG, "dobby", "https://x/1.jpg", "c").execute()
        assert result["success"] is False and calls.await_count == 1

        with patch(f"{MOD}.run_action", new=AsyncMock(return_value={"success": True, "data": {}})) as calls:
            result = await draft_post("toolset", CONFIG, "dobby", "https://x/1.jpg", "c").execute()
        assert result["success"] is False and "container" in result["error"] and calls.await_count == 1

    asyncio.run(run())


def test_story_from_post_reuses_that_posts_media_as_a_story():
    async def run():
        with patch(f"{MOD}.run_action", new=AsyncMock(return_value=MEDIA)):
            pending = await story_from_post("toolset", CONFIG, "dobby", 2)
        assert pending.label == "Instagram story"
        assert "Featuring post #2" in pending.preview and "https://cdn/2.mp4" in pending.preview

        calls = AsyncMock(side_effect=[{"success": True, "data": {"id": "c"}}, {"success": True, "data": {}}])
        with patch(f"{MOD}.run_action", calls):
            await pending.execute()
        params = calls.await_args_list[0].args[2]
        assert params["media_type"] == "STORIES" and params["video_url"] == "https://cdn/2.mp4"

        with patch(f"{MOD}.run_action", new=AsyncMock(return_value=MEDIA)):
            with pytest.raises(UserError, match="no post #9"):
                await story_from_post("toolset", CONFIG, "dobby", 9)
            with pytest.raises(UserError, match="carousel"):
                await story_from_post("toolset", CONFIG, "dobby", 3)

    asyncio.run(run())


def test_local_tools_validate_queue_and_report_config_problems():
    ctx = RunContext(AsyncMock(), "10", "40", "1", toolset="toolset", config=CONFIG)

    async def run():
        with patch(f"{MOD}.run_action", new=AsyncMock(return_value=MEDIA)):
            listed = await LIST_POSTS_TOOL.handler(ctx, {})
            assert listed["success"] and len(listed["posts"]) == 3

            assert (await DRAFT_POST_TOOL.handler(ctx, {"image_url": "ftp://x", "caption": "c"}))[
                "success"
            ] is False
            queued = await DRAFT_POST_TOOL.handler(ctx, {"image_url": "https://x/1.jpg", "caption": "c"})
            assert queued["queued"] is True

            assert (await DRAFT_STORY_TOOL.handler(ctx, {}))["success"] is False
            queued = await DRAFT_STORY_TOOL.handler(ctx, {"post_number": 1})
            assert queued["queued"] is True and "Featuring post #1" in queued["preview"]
            assert (await DRAFT_STORY_TOOL.handler(ctx, {"post_number": 42}))["success"] is False
        assert [p.label for p in ctx.pending] == ["Instagram post", "Instagram story"]

        bare = RunContext(
            AsyncMock(),
            "10",
            "40",
            "1",
            toolset="toolset",
            config=SimpleNamespace(instagram_user_id="", composio_entity="dobby"),
        )
        result = await DRAFT_POST_TOOL.handler(bare, {"image_url": "https://x/1.jpg", "caption": "c"})
        assert result["success"] is False and "INSTAGRAM_USER_ID" in result["error"]

    asyncio.run(run())


def test_integration_exposes_no_direct_actions():
    assert INTEGRATION.actions == ()
    assert len(INTEGRATION.local_tools) == 3
