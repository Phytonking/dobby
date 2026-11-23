"""Tests for the Drive package: trash and share only queue; nothing runs until execute()."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.integrations.base import RunContext
from bot.integrations.google_drive import ACTIONS, INTEGRATION
from bot.integrations.google_drive.tools import (
    GET_METADATA,
    SHARE_FILE,
    SHARE_TOOL,
    TRASH_FILE,
    TRASH_TOOL,
)

MOD = "bot.integrations.google_drive.tools"


def make_ctx():
    return RunContext(
        session=None,
        guild_id="1",
        channel_id="2",
        discord_user_id="3",
        toolset="toolset",
        config=SimpleNamespace(composio_entity="dobby"),
    )


def meta(name="Q3 roadmap"):
    return {"success": True, "data": {"response_data": {"name": name}}}


def test_destructive_actions_are_not_exposed_directly():
    assert TRASH_FILE not in ACTIONS and SHARE_FILE not in ACTIONS
    assert {t.name for t in INTEGRATION.local_tools} == {"trash_drive_file", "share_drive_file"}


def test_trash_queues_a_preview_with_the_file_name_and_runs_only_on_execute():
    async def run():
        ctx = make_ctx()
        calls = AsyncMock(side_effect=[meta(), {"success": True}])
        with patch(f"{MOD}.run_action", calls):
            result = await TRASH_TOOL.handler(ctx, {"file_id": "f1"})
            assert result["queued"] and calls.await_count == 1  # only the metadata lookup
            assert ctx.pending[0].preview == "Move to trash: Q3 roadmap"
            assert (await ctx.pending[0].execute())["success"] is True
        assert calls.await_args_list[0].args[1] == GET_METADATA
        assert calls.await_args_list[1].args == ("toolset", TRASH_FILE, {"file_id": "f1"}, "dobby")

    asyncio.run(run())


def test_share_validates_input_and_queues_exact_grant():
    async def run():
        ctx = make_ctx()
        with patch(f"{MOD}.run_action", new=AsyncMock()) as calls:
            for bad in (
                {"file_id": "f1", "email": "not-an-email"},
                {"file_id": "f1", "email": "a@b.co", "role": "owner"},
                {"email": "a@b.co"},
            ):
                assert (await SHARE_TOOL.handler(ctx, bad))["success"] is False
            assert calls.await_count == 0 and not ctx.pending

        calls = AsyncMock(side_effect=[meta(), {"success": True}])
        with patch(f"{MOD}.run_action", calls):
            await SHARE_TOOL.handler(ctx, {"file_id": "f1", "email": "sam@example.com", "role": "writer"})
            assert ctx.pending[0].preview == "Share Q3 roadmap with sam@example.com as writer"
            await ctx.pending[0].execute()
        assert calls.await_args_list[1].args[1:3] == (
            SHARE_FILE,
            {"file_id": "f1", "type": "user", "role": "writer", "email_address": "sam@example.com"},
        )

    asyncio.run(run())


def test_unknown_file_queues_nothing():
    async def run():
        ctx = make_ctx()
        with patch(f"{MOD}.run_action", new=AsyncMock(return_value={"success": False, "error": "404"})):
            assert (await TRASH_TOOL.handler(ctx, {"file_id": "nope"}))["error"] == "404"
        with patch(f"{MOD}.run_action", new=AsyncMock(return_value={"success": True, "data": {}})):
            assert (await TRASH_TOOL.handler(ctx, {"file_id": "nope"}))["success"] is False
        assert not ctx.pending

    asyncio.run(run())
