import logging
import os

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import RedirectResponse

from ..auth import get_current_user, require_admin
from ..config import DASHBOARD_URL
from ..models import User
from ..schemas import IntegrationOut

logger = logging.getLogger(__name__)
router = APIRouter()

COMPOSIO_API_KEY = os.environ.get("COMPOSIO_API_KEY", "")
COMPOSIO_ENTITY_ID = os.environ.get("COMPOSIO_ENTITY_ID", "dobby")

PROVIDER_MAP = {
    "googlecalendar": "googlecalendar",
    "github": "github",
    "notion": "notion",
}


def _browser_origin(request: Request) -> str:
    # The /api rewrite sets x-forwarded-host; callbacks must return there to carry the session cookie.
    host = request.headers.get("x-forwarded-host")
    if not host:
        return DASHBOARD_URL
    proto = request.headers.get("x-forwarded-proto", "http").split(",")[0].strip()
    return f"{proto}://{host}"


def _client():
    from composio import Composio

    return Composio(api_key=COMPOSIO_API_KEY)


def _get_session():
    return _client().tool_router.create(
        user_id=COMPOSIO_ENTITY_ID,
        toolkits=list(PROVIDER_MAP.values()),
    )


def _list_accounts(client, toolkit=None, statuses=None):
    params = {"user_ids": [COMPOSIO_ENTITY_ID]}
    if toolkit:
        params["toolkit_slugs"] = [toolkit]
    if statuses:
        params["statuses"] = statuses
    items = []
    while True:
        page = client.connected_accounts.list(**params)
        items.extend(page.items)
        if not page.next_cursor:
            return items
        params["cursor"] = page.next_cursor


def _active_since() -> dict[str, str]:
    """Toolkit slug -> earliest created_at among Dobby's ACTIVE connections."""
    since = {}
    for account in _list_accounts(_client(), statuses=["ACTIVE"]):
        slug = account.toolkit.slug
        since[slug] = min(since.get(slug, account.created_at), account.created_at)
    return since


def _disconnect(toolkit: str) -> None:
    from composio.exceptions import (
        ComposioConnectedAccountNotRevokableError,
        ComposioConnectedAccountRevocationNotSupportedError,
    )

    client = _client()
    # Every status, not just ACTIVE, so failed/expired leftovers go too.
    for account in _list_accounts(client, toolkit):
        try:
            client.connected_accounts.revoke(account.id)
        except (ComposioConnectedAccountRevocationNotSupportedError, ComposioConnectedAccountNotRevokableError):
            pass  # upstream revoke is best effort; the delete is what cuts Dobby off
        client.connected_accounts.delete(account.id)


@router.get("", response_model=list[IntegrationOut])
async def list_integrations(_user: User = Depends(get_current_user)):
    if not COMPOSIO_API_KEY:
        return []
    try:
        since = await run_in_threadpool(_active_since)
    except Exception:
        logger.exception("Failed to list Composio connections")
        raise HTTPException(status_code=502, detail="Could not reach Composio")

    return [
        IntegrationOut(provider=provider, connected_at=since[toolkit])
        for provider, toolkit in PROVIDER_MAP.items()
        if toolkit in since
    ]


@router.get("/{provider}/connect")
async def connect_integration(
    provider: str,
    request: Request,
    _admin: User = Depends(require_admin),
):
    if not COMPOSIO_API_KEY:
        raise HTTPException(status_code=503, detail="Composio not configured")

    toolkit = PROVIDER_MAP.get(provider)
    if not toolkit:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown provider '{provider}'. Use: {', '.join(sorted(PROVIDER_MAP))}",
        )

    try:
        session = _get_session()
        callback = f"{_browser_origin(request)}/api/integrations/{provider}/callback"
        conn_req = session.authorize(toolkit, callback_url=callback)
        redirect_url = getattr(conn_req, "redirect_url", None)

        if not redirect_url:
            conn_status = getattr(conn_req, "status", "")
            if conn_status.upper() in ("ACTIVE", "CONNECTED"):
                return RedirectResponse(url=f"/dashboard/integrations?connected={provider}")
            raise ValueError(f"No redirect URL from Composio (status={conn_status})")

    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Composio connect failed for %s: %s", provider, exc)
        raise HTTPException(status_code=502, detail=f"Composio error: {exc}")

    return RedirectResponse(url=redirect_url)


@router.get("/{provider}/callback")
async def integration_callback(provider: str, _user: User = Depends(get_current_user)):
    toolkit = PROVIDER_MAP.get(provider)
    if not toolkit:
        raise HTTPException(status_code=400, detail=f"Unknown provider '{provider}'")

    # Composio's stored state decides success; its redirect query params aren't trusted.
    try:
        connected = toolkit in await run_in_threadpool(_active_since)
    except Exception:
        logger.exception("Composio status check failed after %s callback", provider)
        connected = False
    if not connected:
        logger.warning("Composio callback for %s without an ACTIVE connection", provider)

    outcome = "connected" if connected else "error"
    return RedirectResponse(url=f"/dashboard/integrations?{outcome}={provider}")


@router.delete("/{provider}", status_code=204)
async def disconnect_integration(provider: str, _admin: User = Depends(require_admin)):
    toolkit = PROVIDER_MAP.get(provider)
    if not toolkit:
        raise HTTPException(status_code=400, detail=f"Unknown provider '{provider}'")
    if not COMPOSIO_API_KEY:
        raise HTTPException(status_code=503, detail="Composio not configured")

    try:
        await run_in_threadpool(_disconnect, toolkit)
    except Exception:
        logger.exception("Composio disconnect failed for %s", provider)
        raise HTTPException(status_code=502, detail="Could not disconnect via Composio")
