# Assumes composio-core>=0.7.0. The exact API shape (dict vs object) may vary;
# both branches are handled below.
from composio import ComposioToolSet, App
from google.genai import types

APPS = [App.GOOGLECALENDAR, App.GITHUB, App.NOTION]


def get_toolset(api_key: str) -> ComposioToolSet:
    return ComposioToolSet(api_key=api_key)


def get_gemini_tools(toolset: ComposioToolSet) -> list[types.Tool]:
    """Convert Composio action schemas to Gemini function declarations."""
    actions = toolset.get_tools(apps=APPS)
    declarations = []
    for action in actions:
        if isinstance(action, dict):
            name = action.get("name", "")
            description = action.get("description", "")
            parameters = action.get("parameters")
        else:
            name = action.name
            description = getattr(action, "description", "")
            parameters = getattr(action, "parameters", None)
        if not name:
            continue
        kwargs = {"name": name, "description": description}
        if parameters is not None:
            kwargs["parameters"] = parameters
        declarations.append(types.FunctionDeclaration(**kwargs))
    return [types.Tool(function_declarations=declarations)]


def execute_tool(toolset: ComposioToolSet, tool_name: str, params: dict, entity_id: str) -> dict:
    try:
        result = toolset.execute_action(
            action=tool_name,
            params=params,
            entity_id=entity_id,
        )
        return {"success": True, "data": result}
    except Exception as exc:
        return {"success": False, "error": str(exc)}
