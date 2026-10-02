"""The gate in front of anything Dobby publishes: a preview with Confirm / Cancel buttons.

Only the requester may press them; nothing runs until Confirm; the buttons expire after two minutes.
"""

import logging
import time

import discord

from ...db import SessionLocal
from ...memory import record_action
from ...voice import say
from ..base import PendingAction

log = logging.getLogger("confirm")

TIMEOUT_SECONDS = 120


def preview_text(pending: list[PendingAction]) -> str:
    blocks = [f"**{action.label}**\n{action.preview}" for action in pending]
    return say("preview_intro") + "\n\n" + "\n\n".join(blocks)


class ConfirmView(discord.ui.View):
    def __init__(self, pending: list[PendingAction], requester_id: int, guild_id: str, channel_id: str):
        super().__init__(timeout=TIMEOUT_SECONDS)
        self.pending = pending
        self.requester_id = requester_id
        self.guild_id = guild_id
        self.channel_id = channel_id
        self._claimed = False
        self._expires_at = time.monotonic() + TIMEOUT_SECONDS
        self.message: discord.Message | None = None  # set by the sender so timeouts can edit it

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.requester_id:
            await interaction.response.send_message(say("confirm_not_requester"), ephemeral=True)
            return False
        return True

    def _disable(self):
        for child in self.children:
            child.disabled = True

    async def _claim(self, interaction):
        if not await self.interaction_check(interaction):
            return False
        if self._claimed or time.monotonic() >= self._expires_at:
            await interaction.response.send_message(say("confirm_expired"), ephemeral=True)
            return False
        # No await between checking and claiming: concurrent callbacks cannot both win.
        self._claimed = True
        self._disable()
        return True

    @discord.ui.button(label="Confirm", style=discord.ButtonStyle.success)
    async def confirm(self, interaction: discord.Interaction, _button: discord.ui.Button):
        if not await self._claim(interaction):
            return
        await interaction.response.edit_message(content=say("working"), view=self)
        lines = []
        for action in self.pending:
            start = time.monotonic()
            try:
                result = await action.execute()
                ok = bool(result.get("success"))
                if not ok:
                    log.warning("publish_failed integration=%s", action.integration)
            except Exception as exc:
                log.warning("publish_failed integration=%s type=%s", action.integration, type(exc).__name__)
                ok = False
            try:
                async with SessionLocal() as session:
                    await record_action(
                        session,
                        discord_id=str(self.requester_id),
                        guild_id=self.guild_id,
                        channel_id=self.channel_id,
                        tool=f"{action.integration}.publish",
                        status="ok" if ok else "error",
                        duration_ms=int((time.monotonic() - start) * 1000),
                    )
                    await session.commit()
            except Exception as exc:
                log.warning("audit_failed type=%s", type(exc).__name__)
            lines.append(say("published" if ok else "publish_failed", label=action.label))
        await interaction.edit_original_response(content="\n".join(lines)[:2000], view=self)
        self.stop()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, _button: discord.ui.Button):
        if not await self._claim(interaction):
            return
        await interaction.response.edit_message(content=say("cancelled"), view=self)
        self.stop()

    async def on_timeout(self):
        if self._claimed:
            return
        self._claimed = True
        self._disable()
        self.stop()
        if self.message is not None:
            try:
                await self.message.edit(content=say("confirm_expired"), view=self)
            except discord.HTTPException:
                pass
