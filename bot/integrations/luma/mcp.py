"""A small client for Luma's MCP server (https://mcp.luma.com), plus Dobby's Luma OAuth tokens.

MCP over Streamable HTTP is JSON-RPC 2.0 POSTed to one URL; the server answers with plain JSON or
a short Server-Sent-Events stream. Dobby only needs initialize, tools/list and tools/call, so this
speaks exactly that with httpx instead of pulling the full MCP SDK (and its web stack) into the bot.

Tokens come from the single-row ``luma_connection`` table the dashboard fills when an admin
connects Luma. They are refreshed here shortly before they expire; Luma rotates refresh tokens,
so the new pair is written back under a row lock so two refreshes can't race.
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import httpx
import sqlalchemy as sa

from ...db import SessionLocal
from ...models import UserError

log = logging.getLogger("luma")

PROTOCOL_VERSION = "2025-06-18"
TIMEOUT = 30.0
CATALOG_TTL = 600.0  # seconds between tools/list refreshes
REFRESH_MARGIN = timedelta(seconds=60)

NOT_CONNECTED = "Luma isn't connected. An admin can connect it on the dashboard's service-accounts page."
RECONNECT = "Dobby's Luma connection has expired. An admin needs to reconnect Luma on the dashboard."


def mcp_url() -> str:
    return os.environ.get("LUMA_MCP_URL", "").strip().rstrip("/") or "https://mcp.luma.com"


class McpError(Exception):
    """The server answered, but with an error."""


class Unauthorized(Exception):
    """401 from the MCP server: the access token is no longer accepted."""


# ---------------------------------------------------------------------------
# Tokens
# ---------------------------------------------------------------------------


class TokenStore:
    """Reads Dobby's Luma tokens and refreshes them in place."""

    def __init__(self, session_factory=None, http_factory=None):
        self._sessions = session_factory or SessionLocal
        self._http = http_factory or (lambda: httpx.AsyncClient(timeout=TIMEOUT))

    async def access_token(self, *, force_refresh: bool = False) -> str:
        async with self._sessions() as session:
            row = (
                (await session.execute(sa.text("SELECT * FROM luma_connection WHERE id = 1 FOR UPDATE")))
                .mappings()
                .first()
            )
            if row is None:
                raise UserError(NOT_CONNECTED)
            expires = row["expires_at"]
            fresh = expires is None or expires - REFRESH_MARGIN > datetime.now(timezone.utc)
            if fresh and not force_refresh:
                return row["access_token"]
            if not row["refresh_token"]:
                raise UserError(RECONNECT)
            tokens = await self._refresh(row)
            expires_in = tokens.get("expires_in")
            await session.execute(
                sa.text(
                    "UPDATE luma_connection SET access_token = :a, refresh_token = :r, expires_at = :e, "
                    "updated_at = now() WHERE id = 1"
                ),
                {
                    "a": tokens["access_token"],
                    # Keep the old refresh token if the server didn't rotate it.
                    "r": tokens.get("refresh_token") or row["refresh_token"],
                    "e": datetime.now(timezone.utc) + timedelta(seconds=int(expires_in))
                    if expires_in
                    else None,
                },
            )
            await session.commit()
            log.info("luma_token_refreshed")
            return tokens["access_token"]

    async def _refresh(self, row) -> dict:
        try:
            async with self._http() as http:
                response = await http.post(
                    row["token_endpoint"],
                    data={
                        "grant_type": "refresh_token",
                        "refresh_token": row["refresh_token"],
                        "client_id": row["client_id"],
                        "resource": row["resource"],
                    },
                    headers={"Accept": "application/json"},
                )
        except httpx.HTTPError as exc:
            raise UserError(f"Couldn't reach Luma ({type(exc).__name__}). Try again in a moment.") from None
        if response.status_code in (400, 401):
            # invalid_grant: revoked, expired or already rotated away. Only a fresh sign-in helps.
            log.warning("luma_refresh_rejected status=%s", response.status_code)
            raise UserError(RECONNECT)
        if response.status_code != 200 or not response.json().get("access_token"):
            log.warning("luma_refresh_failed status=%s", response.status_code)
            raise UserError("Luma didn't refresh Dobby's sign-in. Try again in a moment.")
        return response.json()


# ---------------------------------------------------------------------------
# MCP over Streamable HTTP
# ---------------------------------------------------------------------------


def _parse(response: httpx.Response, request_id: int) -> dict:
    """The JSON-RPC response with `request_id`, from a JSON body or an SSE stream."""
    kind = response.headers.get("content-type", "")
    if "text/event-stream" in kind:
        data: list[str] = []
        for line in response.text.splitlines() + [""]:
            if line.startswith("data:"):
                data.append(line[5:].lstrip())
            elif line == "" and data:
                message = json.loads("\n".join(data))
                data = []
                if isinstance(message, dict) and message.get("id") == request_id:
                    return message
        raise McpError("Luma's MCP server closed the stream without answering")
    message = response.json()
    if isinstance(message, list):  # a batch: pick ours
        message = next((m for m in message if m.get("id") == request_id), {})
    return message


@dataclass
class McpSession:
    url: str
    token: str
    http: httpx.AsyncClient
    session_id: str | None = None
    protocol: str = PROTOCOL_VERSION
    _next_id: int = 0

    def _headers(self) -> dict:
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
        }
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
            headers["MCP-Protocol-Version"] = self.protocol
        return headers

    async def _send(self, payload: dict) -> httpx.Response:
        response = await self.http.post(self.url, json=payload, headers=self._headers())
        if response.status_code == 401:
            raise Unauthorized()
        if response.status_code == 404 and self.session_id:
            raise McpError("Luma's MCP session ended")
        if response.status_code >= 400 and response.status_code != 202:
            raise McpError(f"Luma's MCP server returned {response.status_code}")
        return response

    async def request(self, method: str, params: dict | None = None) -> dict:
        self._next_id += 1
        payload = {"jsonrpc": "2.0", "id": self._next_id, "method": method}
        if params is not None:
            payload["params"] = params
        message = _parse(await self._send(payload), self._next_id)
        if "error" in message:
            raise McpError(str(message["error"].get("message") or "Luma returned an error")[:300])
        return message.get("result") or {}

    async def initialize(self) -> None:
        self._next_id += 1
        payload = {
            "jsonrpc": "2.0",
            "id": self._next_id,
            "method": "initialize",
            "params": {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "dobby", "version": "1"},
            },
        }
        response = await self._send(payload)
        self.session_id = response.headers.get("mcp-session-id")
        message = _parse(response, self._next_id)
        if "error" in message:
            raise McpError(str(message["error"].get("message") or "initialize failed")[:300])
        self.protocol = (message.get("result") or {}).get("protocolVersion") or PROTOCOL_VERSION
        await self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})

    async def close(self) -> None:
        if self.session_id:
            try:
                await self.http.delete(self.url, headers=self._headers())
            except httpx.HTTPError:
                pass


@dataclass(frozen=True)
class LumaTool:
    name: str
    title: str
    description: str
    input_schema: dict
    read_only: bool  # only when Luma marks it readOnlyHint; anything unmarked needs Confirm


@dataclass
class LumaClient:
    """What the bot's Luma tools use: the catalog, and calls with automatic sign-in refresh."""

    tokens: TokenStore = field(default_factory=TokenStore)
    url: str = field(default_factory=mcp_url)
    http_factory: object = None
    _catalog: tuple[LumaTool, ...] = ()
    _catalog_at: float = 0.0

    def _http(self) -> httpx.AsyncClient:
        return self.http_factory() if self.http_factory else httpx.AsyncClient(timeout=TIMEOUT)

    async def _with_session(self, work):
        """Run `work(session)` in a fresh MCP session, refreshing the token once on a 401."""
        for attempt in (0, 1):
            token = await self.tokens.access_token(force_refresh=attempt == 1)
            async with self._http() as http:
                session = McpSession(self.url, token, http)
                try:
                    await session.initialize()
                    return await work(session)
                except Unauthorized:
                    if attempt == 1:
                        raise UserError(RECONNECT) from None
                    log.info("luma_mcp_unauthorized_retrying")
                except httpx.HTTPError as exc:
                    raise UserError(
                        f"Couldn't reach Luma ({type(exc).__name__}). Try again in a moment."
                    ) from None
                except McpError as exc:
                    raise UserError(f"Luma: {exc}") from None
                finally:
                    await session.close()

    async def tools(self, *, refresh: bool = False) -> tuple[LumaTool, ...]:
        if self._catalog and not refresh and time.monotonic() - self._catalog_at < CATALOG_TTL:
            return self._catalog

        async def list_all(session: McpSession):
            found, cursor = [], None
            while True:
                result = await session.request("tools/list", {"cursor": cursor} if cursor else {})
                found.extend(result.get("tools") or [])
                cursor = result.get("nextCursor")
                if not cursor:
                    return found

        raw = await self._with_session(list_all)
        catalog = []
        for tool in raw:
            if not isinstance(tool, dict) or not tool.get("name"):
                continue
            notes = tool.get("annotations") or {}
            catalog.append(
                LumaTool(
                    name=str(tool["name"]),
                    title=str(tool.get("title") or notes.get("title") or tool["name"]),
                    description=str(tool.get("description") or "")[:600],
                    input_schema=tool.get("inputSchema") or {"type": "object"},
                    read_only=notes.get("readOnlyHint") is True,
                )
            )
        self._catalog, self._catalog_at = tuple(catalog), time.monotonic()
        return self._catalog

    async def call(self, name: str, arguments: dict) -> dict:
        async def run(session: McpSession):
            return await session.request("tools/call", {"name": name, "arguments": arguments})

        return await self._with_session(run)


def result_text(result: dict, limit: int = 8000) -> str:
    """A tools/call result as text for the model: structured content if given, else the text parts."""
    if result.get("structuredContent") is not None:
        text = json.dumps(result["structuredContent"], default=str)
    else:
        parts = []
        for part in result.get("content") or []:
            if part.get("type") == "text":
                parts.append(part.get("text", ""))
            elif part.get("type") == "resource" and isinstance(part.get("resource"), dict):
                parts.append(part["resource"].get("text", ""))
            else:
                parts.append(f"[{part.get('type', 'content')} omitted]")
        text = "\n".join(parts)
    return text if len(text) <= limit else text[:limit] + "\n…(truncated)"


CLIENT = LumaClient()
