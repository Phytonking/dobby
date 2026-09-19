# Uses composio>=0.21.0 (v3 API). The old composio-core 0.7.x is dead (v1 API returns 410).
import logging

log = logging.getLogger("tools")

TOOLKITS = ["googlecalendar", "github", "notion"]


def create_composio_session(api_key: str, entity_id: str):
    from composio import Composio
    client = Composio(api_key=api_key)
    session = client.tool_router.create(
        user_id=entity_id,
        toolkits=TOOLKITS,
    )
    return client, session


def get_gemini_tools(session):
    from google.genai import types
    raw_tools = session.tools()
    declarations = []
    for tool in raw_tools:
        name = getattr(tool, "name", None) or (tool.get("name") if isinstance(tool, dict) else None)
        desc = getattr(tool, "description", None) or (tool.get("description") if isinstance(tool, dict) else None)
        params = getattr(tool, "parameters", None) or (tool.get("parameters") if isinstance(tool, dict) else None)
        if not name:
            continue
        kwargs = {"name": name, "description": desc or ""}
        if params is not None:
            kwargs["parameters"] = params
        declarations.append(types.FunctionDeclaration(**kwargs))
    return [types.Tool(function_declarations=declarations)] if declarations else []


def execute_tool(session, tool_name: str, params: dict) -> dict:
    try:
        result = session.execute(tool_name, arguments=params)
        data = result if isinstance(result, dict) else getattr(result, "data", str(result))
        return {"success": True, "data": data}
    except Exception as exc:
        log.warning("tool_execute_failed tool=%s error=%s", tool_name, type(exc).__name__)
        return {"success": False, "error": str(exc)}
