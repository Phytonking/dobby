"""Thin client for Luma's public API (https://docs.luma.com). Luma has no Composio toolkit or official MCP server."""

import httpx

BASE_URL = "https://public-api.luma.com"
TIMEOUT = 20.0


async def call(
    api_key: str, method: str, path: str, params: dict | None = None, body: dict | None = None
) -> dict:
    """One Luma request. Returns {"success": True, "data": ...} or {"success": False, "error": ...}."""
    if not api_key:
        return {"success": False, "error": "LUMA_API_KEY is not configured"}
    try:
        async with httpx.AsyncClient(base_url=BASE_URL, timeout=TIMEOUT) as client:
            response = await client.request(
                method, path, params=params, json=body, headers={"x-luma-api-key": api_key}
            )
    except httpx.HTTPError as exc:
        return {"success": False, "error": f"Luma request failed: {type(exc).__name__}"}
    if response.status_code == 429:
        return {"success": False, "error": "Luma rate limit hit; try again in a minute"}
    if response.is_error:
        return {"success": False, "error": f"Luma returned {response.status_code}"}
    return {"success": True, "data": response.json()}
