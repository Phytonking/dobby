"""Connect Dobby to Luma's MCP server (https://mcp.luma.com) with OAuth.

Luma isn't a Composio toolkit, so the dashboard runs the OAuth flow itself: authorization code
with PKCE, a public client identified by a Client ID Metadata Document (a JSON file at a public
HTTPS URL that Luma fetches), and refresh tokens. The tokens land in the single-row
``luma_connection`` table; the bot reads, refreshes and rotates them. Admins only, except the
metadata document, which Luma itself must be able to fetch.

Everything about the authorization server is discovered from the MCP server's protected-resource
metadata (RFC 9728) and the server's own metadata (RFC 8414), so Luma can move endpoints.
"""

import base64
from datetime import datetime, timedelta, timezone
import hashlib
import logging
import os
import secrets
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import require_admin
from ..database import get_db
from ..login_config import dashboard_url, local_mode
from ..models import LumaConnection, User

logger = logging.getLogger(__name__)
router = APIRouter()

TIMEOUT = 15.0
SESSION_KEY = "luma_oauth"


def mcp_url() -> str:
    return os.environ.get("LUMA_MCP_URL", "").strip().rstrip("/") or "https://mcp.luma.com"


def api_url() -> str:
    """Where browsers (and Luma, for the metadata document) reach this API."""
    if local_mode():
        return "http://localhost:8000"
    return (os.environ.get("API_URL", "").strip() or "http://localhost:8000").rstrip("/")


def callback_url() -> str:
    return f"{api_url()}/integrations/luma/callback"


def client_id() -> str:
    """The OAuth client ID is the URL of Dobby's metadata document.

    Luma fetches it, so it must be public HTTPS. When this API isn't reachable from the
    internet, host a copy of /integrations/luma/client.json anywhere public and set
    LUMA_CLIENT_ID to its URL.
    """
    return os.environ.get("LUMA_CLIENT_ID", "").strip() or f"{api_url()}/integrations/luma/client.json"


def client_metadata() -> dict:
    return {
        "client_id": client_id(),
        "client_name": "Dobby",
        "client_uri": dashboard_url(),
        "redirect_uris": [callback_url()],
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "token_endpoint_auth_method": "none",
    }


def _http() -> httpx.AsyncClient:
    """Separate so tests can swap in a mock transport."""
    return httpx.AsyncClient(timeout=TIMEOUT)


async def discover() -> dict:
    """The authorization server's metadata, found from the MCP server's resource metadata."""
    async with _http() as http:
        resource = (await http.get(f"{mcp_url()}/.well-known/oauth-protected-resource")).raise_for_status().json()
        issuer = resource["authorization_servers"][0].rstrip("/")
        server = (await http.get(f"{issuer}/.well-known/oauth-authorization-server")).raise_for_status().json()
    server["resource"] = resource.get("resource") or mcp_url()
    return server


def _pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def _back(**query) -> RedirectResponse:
    return RedirectResponse(url=f"{dashboard_url()}/dashboard/integrations?{urlencode(query)}")


class LumaStatus(BaseModel):
    connected: bool
    connected_at: datetime | None = None
    connected_by: str | None = None
    client_id: str
    callback_url: str


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/client.json", include_in_schema=False)
async def client_metadata_document():
    """Dobby's OAuth client metadata. Public: Luma fetches it during sign-in."""
    return JSONResponse(client_metadata(), headers={"Cache-Control": "public, max-age=300"})


@router.get("/status", response_model=LumaStatus)
async def luma_status(db: AsyncSession = Depends(get_db), _admin: User = Depends(require_admin)):
    row = (await db.execute(select(LumaConnection))).scalar_one_or_none()
    return LumaStatus(
        connected=row is not None,
        connected_at=row.connected_at if row else None,
        connected_by=str(row.connected_by) if row and row.connected_by else None,
        client_id=client_id(),
        callback_url=callback_url(),
    )


@router.get("/connect")
async def connect_luma(request: Request, _admin: User = Depends(require_admin)):
    """Send the admin to Luma's consent screen."""
    try:
        server = await discover()
    except Exception as exc:
        logger.error("luma_discovery_failed type=%s", type(exc).__name__)
        raise HTTPException(status_code=502, detail="Could not reach Luma") from None
    if "S256" not in server.get("code_challenge_methods_supported", ["S256"]):
        raise HTTPException(status_code=502, detail="Luma no longer supports PKCE S256")

    verifier, challenge = _pkce()
    state = secrets.token_urlsafe(32)
    request.session[SESSION_KEY] = {
        "state": state,
        "verifier": verifier,
        "issuer": server.get("issuer"),
        "token_endpoint": server["token_endpoint"],
        "revocation_endpoint": server.get("revocation_endpoint"),
        "resource": server["resource"],
        "client_id": client_id(),
    }
    endpoint = server["authorization_endpoint"]
    query = urlencode({
        "response_type": "code",
        "client_id": client_id(),
        "redirect_uri": callback_url(),
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": state,
        "resource": server["resource"],
    })
    # Luma's authorization endpoint already carries a query string (?credential_type=personal).
    return RedirectResponse(url=f"{endpoint}{'&' if '?' in endpoint else '?'}{query}")


@router.get("/callback")
async def luma_callback(
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """Exchange the code for tokens and keep them for the bot."""
    flow = request.session.pop(SESSION_KEY, None)
    params = request.query_params
    if not flow or not params.get("state") or not secrets.compare_digest(params["state"], flow["state"]):
        logger.warning("luma_callback_state_mismatch")
        return _back(error="luma")
    if params.get("error") or not params.get("code"):
        logger.warning("luma_connect_not_completed error=%s", params.get("error", "no_code")[:40])
        return _back(error="luma")
    # RFC 9207: when the server names itself, it must be the one we started with.
    if params.get("iss") and flow.get("issuer") and params["iss"].rstrip("/") != flow["issuer"].rstrip("/"):
        logger.warning("luma_callback_issuer_mismatch")
        return _back(error="luma")

    try:
        async with _http() as http:
            response = await http.post(
                flow["token_endpoint"],
                data={
                    "grant_type": "authorization_code",
                    "code": params["code"],
                    "redirect_uri": callback_url(),
                    "client_id": flow["client_id"],
                    "code_verifier": flow["verifier"],
                    "resource": flow["resource"],
                },
                headers={"Accept": "application/json"},
            )
        if response.status_code != 200:
            logger.error("luma_token_exchange_failed status=%s", response.status_code)
            return _back(error="luma")
        tokens = response.json()
        if not tokens.get("access_token"):
            raise ValueError("no access_token")
    except Exception as exc:
        logger.error("luma_token_exchange_failed type=%s", type(exc).__name__)
        return _back(error="luma")

    expires_in = tokens.get("expires_in")
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=int(expires_in)) if expires_in else None
    try:
        row = (await db.execute(select(LumaConnection))).scalar_one_or_none() or LumaConnection(id=1)
        row.client_id = flow["client_id"]
        row.token_endpoint = flow["token_endpoint"]
        row.revocation_endpoint = flow.get("revocation_endpoint")
        row.resource = flow["resource"]
        row.access_token = tokens["access_token"]
        row.refresh_token = tokens.get("refresh_token")
        row.expires_at = expires_at
        row.connected_by = admin.id
        row.connected_at = datetime.now(timezone.utc)
        row.updated_at = datetime.now(timezone.utc)
        db.add(row)
        await db.commit()
    except Exception:
        logger.exception("Failed to store the Luma connection")
        return _back(error="luma")
    logger.info("luma_connected by=%s", admin.id)
    return _back(connected="luma")


@router.delete("", status_code=204)
async def disconnect_luma(db: AsyncSession = Depends(get_db), _admin: User = Depends(require_admin)):
    """Revoke Dobby's Luma tokens (best effort), then forget them."""
    row = (await db.execute(select(LumaConnection))).scalar_one_or_none()
    if row is None:
        return
    if row.revocation_endpoint:
        try:
            async with _http() as http:
                for token, hint in ((row.refresh_token, "refresh_token"), (row.access_token, "access_token")):
                    if token:
                        await http.post(
                            row.revocation_endpoint,
                            data={"token": token, "token_type_hint": hint, "client_id": row.client_id},
                        )
        except Exception as exc:
            # The row still goes: an admin asked for Dobby to lose access, and without tokens it does.
            logger.warning("luma_revoke_failed type=%s", type(exc).__name__)
    await db.delete(row)
    await db.commit()
