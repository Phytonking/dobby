"""Everything Dobby can reach lives in one folder per service.

Each package exports an ``INTEGRATION`` describing the Composio actions the model may call, local
tools that run in-process, prompt guidance, slash commands and help text. ``build_registry`` turns
the set into what the agent needs. The Discord package is the transport, not an integration.
"""

from dataclasses import dataclass, field

from bot.AIModels import FunctionDeclaration, Tool

from ..composio import declarations_for
from .base import Integration, LocalTool
from . import google_calendar, instagram, linkedin, notion
from .google_calendar.proposals import WRITE_ACTIONS, prepare_calendar_action, with_deferred_invitees

INTEGRATIONS: tuple[Integration, ...] = (
    google_calendar.INTEGRATION,
    notion.INTEGRATION,
    instagram.INTEGRATION,
    linkedin.INTEGRATION,
)


@dataclass(frozen=True)
class ToolRegistry:
    tools: list[Tool]
    local: dict[str, LocalTool] = field(default_factory=dict)
    owner: dict[str, str] = field(default_factory=dict)  # tool name -> integration key
    prompt: str = ""


def build_registry(toolset, integrations: tuple[Integration, ...] = INTEGRATIONS) -> ToolRegistry:
    declarations: list[FunctionDeclaration] = []
    local: dict[str, LocalTool] = {}
    owner: dict[str, str] = {}
    for integration in integrations:
        for declaration in declarations_for(toolset, integration.actions):
            if integration.key == "google_calendar" and WRITE_ACTIONS.get(declaration.name) in (
                "create",
                "update",
            ):
                declaration = with_deferred_invitees(declaration)
            declarations.append(declaration)
            owner[declaration.name] = integration.key
            if integration.key == "google_calendar" and declaration.name in WRITE_ACTIONS:

                async def prepare(ctx, params, name=declaration.name):
                    return await prepare_calendar_action(ctx, name, params)

                local[declaration.name] = LocalTool(declaration, prepare)
        for tool in integration.local_tools:
            declarations.append(tool.declaration)
            local[tool.name] = tool
            owner[tool.name] = integration.key
    tools = [Tool(function_declarations=declarations)] if declarations else []
    prompt = "\n".join(i.prompt.strip() for i in integrations if i.prompt.strip())
    return ToolRegistry(tools=tools, local=local, owner=owner, prompt=prompt)
