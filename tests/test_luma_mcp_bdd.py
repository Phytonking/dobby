"""Behaviour specs for Luma through its MCP server (features/luma_mcp.feature).

Luma is played by a real MCP server from the official `mcp` SDK, served in-process over
Streamable HTTP (no ports), behind a bearer-token check like Luma's. Only the token store is
faked; its SQL is covered against Postgres in tests/integration.
"""

import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest

pytest.importorskip("pytest_bdd")
mcp_sdk = pytest.importorskip("mcp.server.mcpserver")

from mcp.server.mcpserver.exceptions import ToolError  # noqa: E402
from mcp.server.transport_security import TransportSecuritySettings  # noqa: E402
from mcp.types import ToolAnnotations  # noqa: E402
from pytest_bdd import given, parsers, scenarios, then, when  # noqa: E402

from bot.integrations.base import RunContext  # noqa: E402
from bot.integrations.luma import mcp, tools  # noqa: E402
from bot.models import UserError  # noqa: E402

scenarios("features/luma_mcp.feature")

URL = "http://localhost/"


class FakeTokens:
    """Stands in for TokenStore: one current token, and a refresh that rotates it (or fails)."""

    def __init__(self, world):
        self.world = world

    async def access_token(self, *, force_refresh=False):
        if not self.world.connected:
            raise UserError(mcp.NOT_CONNECTED)
        if force_refresh:
            if not self.world.refresh_ok:
                raise UserError(mcp.RECONNECT)
            self.world.refreshes += 1
            self.world.current_token = f"token-{self.world.refreshes + 1}"
            self.world.accepted_token = self.world.current_token
        return self.world.current_token


def fake_luma(world):
    server = mcp_sdk.MCPServer("fake-luma")

    @server.tool(annotations=ToolAnnotations(readOnlyHint=True), description="List the calendar's events")
    def list_events(limit: int = 10) -> dict:
        world.calls.append(("list_events", {"limit": limit}))
        if limit < 1:
            raise ToolError("limit must be positive")
        return {"events": [{"id": "evt-1", "name": "Demo Night"}][:limit]}

    @server.tool(description="Email invitations to an event")  # no annotations: must be confirmed
    def send_invites(event_id: str, emails: list[str]) -> str:
        world.calls.append(("send_invites", {"event_id": event_id, "emails": emails}))
        return f"invited {len(emails)} to {event_id}"

    app = server.streamable_http_app(
        streamable_http_path="/",
        json_response=world.json_response,
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )

    async def guarded(scope, receive, send):
        """Luma answers 401 to any token it doesn't currently accept."""
        if scope["type"] == "http":
            auth = dict(scope["headers"]).get(b"authorization", b"").decode()
            if auth != f"Bearer {world.accepted_token}":
                body = b'{"error":"invalid_token"}'
                await send(
                    {
                        "type": "http.response.start",
                        "status": 401,
                        "headers": [(b"content-type", b"application/json")],
                    }
                )
                await send({"type": "http.response.body", "body": body})
                return
        await app(scope, receive, send)

    return app, guarded


@pytest.fixture()
def world():
    return SimpleNamespace(
        connected=True,
        refresh_ok=True,
        refreshes=0,
        current_token="token-1",
        accepted_token="token-1",
        json_response=False,
        calls=[],
        ctx=None,
        out=None,
        confirmed=None,
        serving=None,
    )


def run(world, work):
    """Serve the fake Luma for the duration of `work(ctx)`."""

    async def go():
        app, world.serving = fake_luma(world)

        async def current(scope, receive, send):
            # Whatever fake Luma is up now: a confirmed action runs after the one that queued it.
            await world.serving(scope, receive, send)

        async with app.router.lifespan_context(app):
            client = mcp.LumaClient(
                tokens=FakeTokens(world),
                url=URL,
                http_factory=lambda: httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=current), base_url=URL
                ),
            )
            if world.ctx is None:
                world.ctx = RunContext(
                    session=None,
                    guild_id="1",
                    channel_id="2",
                    discord_user_id="3",
                    config=SimpleNamespace(luma_client=client),
                )
            else:
                world.ctx.config.luma_client = client
            return await work(world.ctx)

    return asyncio.run(go())


# ---------------------------------------------------------------------------
# Given
# ---------------------------------------------------------------------------


@given("Luma is connected")
def connected(world):
    world.connected = True


@given("Luma is not connected")
def not_connected(world):
    world.connected = False


@given(
    parsers.parse('Luma\'s MCP server offers a read-only "{read}" tool and an unannotated "{change}" tool')
)
def offers(world, read, change):
    assert (read, change) == ("list_events", "send_invites")  # defined in fake_luma


@given(parsers.parse("Luma's MCP server answers with {format}"))
def answers_with(world, format):
    world.json_response = format == "plain JSON"


@given("Luma no longer accepts Dobby's current access token")
def token_rejected(world):
    world.accepted_token = "token-2"  # only a refreshed token works


@given("Luma rejects Dobby's refresh token")
def refresh_rejected(world):
    world.refresh_ok = False


# ---------------------------------------------------------------------------
# When
# ---------------------------------------------------------------------------


@when("Dobby asks which Luma tools there are")
def ask_tools(world):
    world.out = run(world, lambda ctx: tools.list_tools(ctx, {}))


@when(parsers.parse("Dobby calls \"{name}\" with '{arguments}'"))
def call(world, name, arguments):
    world.out = run(world, lambda ctx: tools.call_tool(ctx, {"tool": name, "arguments": arguments}))


@when("the requester confirms")
def confirm(world):
    (pending,) = world.ctx.pending
    world.confirmed = run(world, lambda ctx: pending.execute())


# ---------------------------------------------------------------------------
# Then
# ---------------------------------------------------------------------------


def _offered(world):
    assert world.out["success"], world.out
    return {t["name"]: t for t in world.out["tools"]}


@then(parsers.parse('Dobby is offered "{name}" as read-only'))
def offered_read_only(world, name):
    tool = _offered(world)[name]
    assert tool["read_only"] is True
    assert "limit" in json.dumps(tool["arguments"])


@then(parsers.parse('Dobby is offered "{name}" as a change'))
def offered_change(world, name):
    assert _offered(world)[name]["read_only"] is False


@then(parsers.parse('the call succeeds with "{text}"'))
def succeeds_with(world, text):
    assert world.out["success"] is True, world.out
    assert text in world.out["result"]


@then("nothing is waiting for Confirm")
def nothing_pending(world):
    assert world.ctx.pending == []


@then(parsers.parse('Luma ran "{name}" {count:d} time'))
@then(parsers.parse('Luma ran "{name}" {count:d} times'))
def ran_times(world, name, count):
    assert len([c for c in world.calls if c[0] == name]) == count


@then(parsers.parse('the change is waiting for Confirm with a preview showing "{text}"'))
def waiting(world, text):
    assert world.out["queued"] is True
    (pending,) = world.ctx.pending
    assert pending.integration == "luma"
    assert text in pending.preview and "send_invites" in pending.label


@then(parsers.parse("Luma ran \"{name}\" {count:d} time with '{arguments}'"))
def ran_with(world, name, count, arguments):
    calls = [c for c in world.calls if c[0] == name]
    assert len(calls) == count
    assert calls[0][1] == json.loads(arguments)


@then(parsers.parse('the confirmed change reports "{text}"'))
def confirmed_reports(world, text):
    assert world.confirmed["success"] is True, world.confirmed
    assert text in world.confirmed["result"]


@then(parsers.parse('the call fails mentioning "{text}"'))
def fails(world, text):
    assert world.out["success"] is False, world.out
    assert text in world.out["error"]


@then(parsers.parse("Dobby refreshed its Luma sign-in {count:d} time"))
def refreshed(world, count):
    assert world.refreshes == count
