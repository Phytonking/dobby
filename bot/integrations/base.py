"""Shared shapes for integrations: what a service exposes and how confirm-gated actions are queued."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Awaitable, Callable

from bot.AIModels import FunctionDeclaration
from sqlalchemy.ext.asyncio import AsyncSession

if TYPE_CHECKING:  # avoid importing the Discord client at runtime (it imports the registry)
    from .discord.client import Bot


@dataclass(frozen=True)
class PendingAction:
    """Something a command or the model wants to do that must not happen until the requester confirms."""

    integration: str
    label: str  # short, e.g. "LinkedIn post"
    preview: str  # exactly what will be published, shown in Discord
    execute: Callable[[], Awaitable[dict]]  # runs the Composio call(s); returns {"success": bool, ...}


@dataclass
class RunContext:
    """What a local tool may need during one agent run."""

    session: AsyncSession
    guild_id: str
    channel_id: str
    discord_user_id: str
    toolset: object = None  # Composio client, for tools that build PendingActions
    config: object = None  # bot Config (entity id, Instagram account id, ...)
    pending: list[PendingAction] = field(default_factory=list)
    missing_invitees: dict[str, dict] = field(default_factory=dict)

    def queue(self, action: PendingAction) -> dict:
        """Park an action for confirmation and tell the model what happened."""
        self.pending.append(action)
        return {
            "success": True,
            "queued": True,
            "preview": action.preview,
            "note": "Shown to the requester with Confirm/Cancel buttons. Do not call this again; "
            "tell the user to confirm in Discord.",
        }


LocalHandler = Callable[[RunContext, dict], Awaitable[dict]]


@dataclass(frozen=True)
class LocalTool:
    """A model function that runs in-process instead of through Composio."""

    declaration: FunctionDeclaration
    handler: LocalHandler

    @property
    def name(self) -> str:
        return self.declaration.name


@dataclass(frozen=True)
class Integration:
    key: str
    label: str
    app: str  # Composio toolkit slug
    actions: tuple[str, ...] = ()  # curated Composio actions the model may call directly
    local_tools: tuple[LocalTool, ...] = ()
    prompt: str = ""  # appended to the system prompt
    register_commands: Callable[[Bot], None] | None = None
    help_lines: tuple[str, ...] = ()  # shown by /help
