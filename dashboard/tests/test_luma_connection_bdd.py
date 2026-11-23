"""Behaviour specs for connecting Luma's MCP server from the dashboard (features/luma_connection.feature).

Luma's discovery, token and revocation endpoints are an httpx MockTransport; the database is the
suite's FakeDB.
"""

import base64
import hashlib
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from pytest_bdd import given, parsers, scenarios, then, when

from dashboard.auth import get_current_user
from dashboard.models import LumaConnection

from .conftest import FakeDB, make_user

scenarios("features/luma_connection.feature")

MOD = "dashboard.routers.luma"
AUTHORIZE = "https://luma.com/oauth/consent?credential_type=personal"
TOKEN = "https://api.luma.com/oauth/token"
REVOKE = "https://api.luma.com/oauth/revoke"


@pytest.fixture()
def world(app, client, monkeypatch):
    state = SimpleNamespace(
        app=app, client=client, http=None, requests=[], refuse_code=False, db=None, response=None
    )

    def handler(request: httpx.Request) -> httpx.Response:
        state.requests.append(request)
        url = str(request.url)
        if url == "https://mcp.luma.com/.well-known/oauth-protected-resource":
            return httpx.Response(
                200,
                json={"resource": "https://mcp.luma.com", "authorization_servers": ["https://api.luma.com"]},
            )
        if url == "https://api.luma.com/.well-known/oauth-authorization-server":
            return httpx.Response(
                200,
                json={
                    "issuer": "https://api.luma.com",
                    "authorization_endpoint": AUTHORIZE,
                    "token_endpoint": TOKEN,
                    "revocation_endpoint": REVOKE,
                    "code_challenge_methods_supported": ["S256"],
                    "client_id_metadata_document_supported": True,
                },
            )
        if url == TOKEN:
            if state.refuse_code:
                return httpx.Response(400, json={"error": "invalid_grant"})
            return httpx.Response(
                200,
                json={
                    "access_token": "at-1",
                    "refresh_token": "rt-1",
                    "expires_in": 3600,
                    "token_type": "Bearer",
                },
            )
        if url == REVOKE:
            return httpx.Response(200)
        return httpx.Response(404)

    monkeypatch.setattr(f"{MOD}._http", lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    monkeypatch.delenv("LUMA_CLIENT_ID", raising=False)
    monkeypatch.delenv("LUMA_MCP_URL", raising=False)
    monkeypatch.setenv("LOCAL_MODE", "false")
    state.monkeypatch = monkeypatch
    yield state
    app.dependency_overrides.clear()


def _token_requests(world):
    return [r for r in world.requests if str(r.url) == TOKEN]


def _form(request: httpx.Request) -> dict:
    return {k: v[0] for k, v in parse_qs(request.content.decode()).items()}


def _http_client(world):
    if world.http is None:
        world.db = FakeDB(results=[None, None, None])
        world.http = world.client(world.db)
    return world.http


# ---------------------------------------------------------------------------
# Given
# ---------------------------------------------------------------------------


@given(parsers.parse('the API\'s public address is "{url}"'))
def public_address(world, url):
    world.monkeypatch.setenv("API_URL", url)


@given("Luma publishes its OAuth details")
def luma_publishes(world):
    pass  # served by the mock transport


@given(parsers.parse('LUMA_CLIENT_ID is "{url}"'))
def client_id_override(world, url):
    world.monkeypatch.setenv("LUMA_CLIENT_ID", url)


def _sign_in(world, role):
    world.user = make_user(role=role)

    async def current():
        return world.user

    world.app.dependency_overrides[get_current_user] = current


@given("I am signed in as an admin")
def admin(world):
    _sign_in(world, "admin")


@given("I am signed in as a student")
def student(world):
    _sign_in(world, "student")


@given("I have started connecting Luma")
def started(world):
    start(world)
    query = parse_qs(urlparse(world.response.headers["location"]).query)
    world.state = query["state"][0]
    world.challenge = query["code_challenge"][0]


@given("Luma will refuse the code")
def refuse(world):
    world.refuse_code = True


@given("Dobby is connected to Luma")
def is_connected(world):
    world.row = LumaConnection(
        id=1,
        client_id="https://dobby.example.org/api/integrations/luma/client.json",
        token_endpoint=TOKEN,
        revocation_endpoint=REVOKE,
        resource="https://mcp.luma.com",
        access_token="at-1",
        refresh_token="rt-1",
    )
    world.db = FakeDB(results=[world.row])
    world.http = world.client(world.db)


# ---------------------------------------------------------------------------
# When
# ---------------------------------------------------------------------------


@when("I start connecting Luma")
def start(world):
    world.response = _http_client(world).get("/integrations/luma/connect", follow_redirects=False)


def _callback(world, **query):
    world.response = world.http.get("/integrations/luma/callback", params=query, follow_redirects=False)


@when("Luma sends me back with a code")
def back_with_code(world):
    _callback(world, code="code-1", state=world.state, iss="https://api.luma.com")


@when("Luma sends me back with a code but a different state")
def back_wrong_state(world):
    _callback(world, code="code-1", state="forged-state")


@when(parsers.parse('Luma sends me back with a code from issuer "{issuer}"'))
def back_wrong_issuer(world, issuer):
    _callback(world, code="code-1", state=world.state, iss=issuer)


@when(parsers.parse('Luma sends me back with error "{error}"'))
def back_with_error(world, error):
    _callback(world, error=error, state=world.state)


@when("I disconnect Luma")
def disconnect(world):
    world.response = world.http.delete("/integrations/luma")


@when("Luma fetches Dobby's client metadata")
def fetch_metadata(world):
    world.response = world.client(FakeDB()).get("/integrations/luma/client.json")


# ---------------------------------------------------------------------------
# Then
# ---------------------------------------------------------------------------


def _authorize_query(world):
    assert world.response.status_code == 307, world.response.text
    return parse_qs(urlparse(world.response.headers["location"]).query)


@then("I am sent to Luma's consent screen")
def sent_to_consent(world):
    location = world.response.headers["location"]
    assert location.startswith("https://luma.com/oauth/consent?credential_type=personal&")
    assert _authorize_query(world)["response_type"] == ["code"]


@then(parsers.parse('the request identifies Dobby by "{client_id}"'))
def identifies(world, client_id):
    assert _authorize_query(world)["client_id"] == [client_id]


@then(parsers.parse('the request returns to "{url}"'))
def returns_to(world, url):
    assert _authorize_query(world)["redirect_uri"] == [url]


@then(parsers.parse('the request asks for the "{resource}" resource with an S256 code challenge'))
def pkce(world, resource):
    query = _authorize_query(world)
    assert query["resource"] == [resource]
    assert query["code_challenge_method"] == ["S256"]
    assert len(query["code_challenge"][0]) == 43 and len(query["state"][0]) >= 32


@then("Dobby traded the code using the matching PKCE verifier")
def traded(world):
    (request,) = _token_requests(world)
    form = _form(request)
    assert form["grant_type"] == "authorization_code" and form["code"] == "code-1"
    assert form["client_id"] == "https://dobby.example.org/api/integrations/luma/client.json"
    assert form["redirect_uri"] == "https://dobby.example.org/api/integrations/luma/callback"
    assert form["resource"] == "https://mcp.luma.com"
    # The verifier Dobby sends now hashes to the challenge it showed Luma at the start.
    challenge = base64.urlsafe_b64encode(hashlib.sha256(form["code_verifier"].encode()).digest()).rstrip(b"=")
    assert challenge.decode() == world.challenge


@then(parsers.parse("Dobby's Luma tokens are stored, expiring in about an hour"))
def stored(world):
    (row,) = world.db.added
    assert (row.access_token, row.refresh_token) == ("at-1", "rt-1")
    assert row.token_endpoint == TOKEN and row.revocation_endpoint == REVOKE
    assert row.connected_by == world.user.id
    left = row.expires_at - datetime.now(timezone.utc)
    assert timedelta(minutes=55) < left <= timedelta(hours=1)
    assert world.db.committed


@then(parsers.parse('I land on the service accounts page with "{query}"'))
def lands(world, query):
    assert world.response.status_code == 307
    assert world.response.headers["location"].endswith(f"/dashboard/integrations?{query}")


@then("Dobby never asked Luma for tokens")
def never_asked(world):
    assert _token_requests(world) == []


@then("nothing is stored")
def nothing_stored(world):
    assert world.db.added == [] and not world.db.committed


@then("Luma was asked to revoke the refresh token and the access token")
def revoked(world):
    revokes = [_form(r) for r in world.requests if str(r.url) == REVOKE]
    assert [(f["token"], f["token_type_hint"]) for f in revokes] == [
        ("rt-1", "refresh_token"),
        ("at-1", "access_token"),
    ]


@then("the Luma connection is deleted")
def deleted(world):
    assert world.response.status_code == 204
    assert world.db.deleted == [world.row] and world.db.committed


@then(parsers.parse('it names client "{client_id}" returning to "{callback}"'))
def metadata_names(world, client_id, callback):
    assert world.response.status_code == 200
    doc = world.response.json()
    assert doc["client_id"] == client_id and doc["redirect_uris"] == [callback]
    assert doc["client_name"] == "Dobby"


@then("it is a public client using the authorization code and refresh token grants")
def metadata_public(world):
    doc = json.loads(world.response.text)
    assert doc["token_endpoint_auth_method"] == "none"
    assert doc["grant_types"] == ["authorization_code", "refresh_token"]


@then(parsers.parse("the request is refused with {code:d}"))
def refused(world, code):
    assert world.response.status_code == code
