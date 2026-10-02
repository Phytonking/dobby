"""Run the real entry point with only external transport boundaries replaced.

Not a live Discord/Composio compatibility check. The internal test network also
blocks outbound calls, so a missing stub fails instead of using a real account.
"""

import asyncio
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, "/app")

from bot.composio import run_action
from bot.integrations.discord.client import Bot
from bot.main import main


class Tools:
    def get_raw_composio_tools(self, *, tools):
        return [SimpleNamespace(slug=name, description=name, input_parameters={}) for name in tools]

    def execute(self, **kwargs):
        cache = Path(os.environ["COMPOSIO_CACHE_DIR"])
        cache.mkdir(parents=True, exist_ok=True)
        (cache / "regression-write").write_text("ok")
        return {"successful": True, "data": {"ok": True}}


async def start(self, token, *, reconnect=True):
    assert os.getuid() != 0
    try:
        Path("/app/forbidden-write").write_text("bad")
    except OSError:
        pass
    else:
        raise AssertionError("Bot root filesystem is writable")

    async def sync(*, guild):
        assert guild.id == self.config.guild
        return []

    with patch.object(self.tree, "sync", side_effect=sync):
        await self.setup_hook()
    result = await run_action(self.toolset, "REGRESSION_CACHE_WRITE", {}, self.config.composio_entity)
    assert result["success"]
    await self.on_ready()
    print("regression_bot_ready", flush=True)
    await asyncio.Event().wait()


with patch("bot.integrations.discord.client.get_toolset", return_value=SimpleNamespace(tools=Tools())):
    with patch.object(Bot, "start", start):
        main()
