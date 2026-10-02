"""Tests for the LinkedIn package: drafts are PendingActions and nothing runs until execute()."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.integrations.base import RunContext
from bot.integrations.linkedin import INTEGRATION
from bot.integrations.linkedin.publish import (
    CREATE_POST,
    DRAFT_TOOL,
    GET_ME,
    MAX_CHARS,
    author_urn,
    draft_post,
)

MOD = "bot.integrations.linkedin.publish"


def test_author_urn_handles_the_shapes_composio_may_return():
    assert author_urn({"data": {"author": "urn:li:person:abc"}}) == "urn:li:person:abc"
    assert author_urn({"response_data": {"sub": "xyz"}}) == "urn:li:person:xyz"
    assert author_urn({"data": {}}) is None


def test_draft_post_does_nothing_until_executed_then_resolves_author_and_posts():
    pending = draft_post("toolset", "dobby", "  We shipped v2!  ")
    assert pending.integration == "linkedin" and pending.preview == "We shipped v2!"

    async def run():
        calls = AsyncMock(
            side_effect=[
                {"success": True, "data": {"sub": "me123"}},
                {"success": True, "data": {"id": "urn:li:share:1"}},
            ]
        )
        with patch(f"{MOD}.run_action", calls):
            result = await pending.execute()
        assert result["success"] is True
        assert calls.await_args_list[0].args == ("toolset", GET_ME, {}, "dobby")
        name, params = calls.await_args_list[1].args[1], calls.await_args_list[1].args[2]
        assert name == CREATE_POST
        assert params["author"] == "urn:li:person:me123" and params["commentary"] == "We shipped v2!"

    asyncio.run(run())


def test_draft_post_stops_when_the_author_cannot_be_resolved():
    async def run():
        with patch(f"{MOD}.run_action", new=AsyncMock(return_value={"success": True, "data": {}})) as calls:
            result = await draft_post("toolset", "dobby", "hi").execute()
        assert result["success"] is False and "author" in result["error"]
        assert calls.await_count == 1  # no post attempted

        with patch(
            f"{MOD}.run_action", new=AsyncMock(return_value={"success": False, "error": "no account"})
        ):
            assert (await draft_post("toolset", "dobby", "hi").execute())["error"] == "no account"

    asyncio.run(run())


def test_draft_tool_validates_and_queues():
    config = SimpleNamespace(composio_entity="dobby")
    ctx = RunContext(AsyncMock(), "10", "40", "1", toolset="toolset", config=config)

    async def run():
        assert (await DRAFT_TOOL.handler(ctx, {"text": "  "}))["success"] is False
        assert (await DRAFT_TOOL.handler(ctx, {"text": "x" * (MAX_CHARS + 1)}))["success"] is False
        assert ctx.pending == []
        result = await DRAFT_TOOL.handler(ctx, {"text": "hello"})
        assert result["queued"] is True and result["preview"] == "hello"
        assert [p.label for p in ctx.pending] == ["LinkedIn post"]

    asyncio.run(run())


def test_integration_exposes_only_the_draft_tool():
    assert INTEGRATION.actions == ()
    assert [t.name for t in INTEGRATION.local_tools] == ["draft_linkedin_post"]
