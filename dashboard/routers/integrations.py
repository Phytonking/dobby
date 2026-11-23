"""Dobby's service accounts.

One Composio entity (COMPOSIO_ENTITY_ID) owns the Google Calendar, Drive, Docs, Notion, Instagram and
LinkedIn connections the bot acts through. Nobody links a personal account here, so every route
is admin-only and the table holds one row per provider.
"""

import asyncio
import logging
import os

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import require_admin
from ..database import get_db
from ..login_config import dashboard_url
from ..models import Integration, User
from ..schemas import IntegrationOut

logger = logging.getLogger(__name__)
router = APIRouter()

COMPOSIO_API_KEY = os.environ.get("COMPOSIO_API_KEY", "")
# Must match the bot's COMPOSIO_ENTITY_ID so tool calls find these connections.
ENTITY_ID = os.environ.get("COMPOSIO_ENTITY_ID", "dobby").strip() or "dobby"

# UI provider keys mapped to current Composio toolkit slugs.
SUPPORTED_PROVIDERS = {
    "google_calendar": "googlecalendar",
    "google_drive": "googledrive",
    "google_docs": "googledocs",
    "notion": "notion",
    "instagram": "instagram",
    "linkedin": "linkedin",
}


def _get_toolkit(provider: str) -> str:
    app = SUPPORTED_PROVIDERS.get(provider)
    if app is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported provider '{provider}'. Supported: {sorted(SUPPORTED_PROVIDERS)}",
        )
    return app


def _get_composio():
    if not COMPOSIO_API_KEY:
        raise HTTPException(status_code=503, detail="Composio is not configured")
    from composio import Composio

    return Composio(api_key=COMPOSIO_API_KEY)


def _authorize(toolkit: str, callback_url: str) -> str:
    session = _get_composio().create(user_id=ENTITY_ID)
    connection = session.authorize(toolkit, callback_url=callback_url)
    if not connection.redirect_url:
        raise RuntimeError("Composio did not return a Connect Link")
    return connection.redirect_url


def _connection_is_active(toolkit: str, account_id: str) -> bool:
    # Filter server-side by identity; user_id on retrieved accounts is deprecated.
    accounts = _get_composio().connected_accounts.list(
        user_ids=[ENTITY_ID],
        toolkit_slugs=[toolkit],
        connected_account_ids=[account_id],
        statuses=["ACTIVE"],
    )
    return any(account.id == account_id and account.status == "ACTIVE" for account in accounts.items)


# Best first: when one toolkit has several accounts, the healthiest one decides its status.
_STATUS_RANK = {"ACTIVE": 0, "INITIATED": 1, "INITIALIZING": 1, "EXPIRED": 2, "FAILED": 2}
_LIVE_STATUS = {"ACTIVE": "connected", "INITIATED": "pending", "INITIALIZING": "pending"}


def _live_connections() -> dict[str, dict]:
    """Every toolkit the service entity has an account for in Composio, keyed by toolkit slug.

    Composio is the source of truth: a connection made in Composio's own UI, or one whose
    callback never reached the dashboard, is still a connection the bot can use.
    """
    client = _get_composio()
    found: dict[str, dict] = {}
    cursor = None
    while True:
        params = {"user_ids": [ENTITY_ID]}
        if cursor:
            params["cursor"] = cursor
        page = client.connected_accounts.list(**params)
        for account in page.items:
            toolkit = getattr(getattr(account, "toolkit", None), "slug", None)
            state = str(getattr(account, "status", "") or "").upper()
            # REVOKED/INACTIVE: deliberately switched off or removed, i.e. not connected.
            if not toolkit or state in ("REVOKED", "INACTIVE") or getattr(account, "is_disabled", False):
                continue
            best = found.get(toolkit)
            if best is None or _STATUS_RANK.get(state, 9) < _STATUS_RANK.get(best["raw"], 9):
                found[toolkit] = {"raw": state, "created_at": getattr(account, "created_at", None)}
        cursor = getattr(page, "next_cursor", None)
        if not cursor:
            break
    return found


def _disconnect_accounts(toolkit: str) -> None:
    client = _get_composio()
    # Collect before deleting so pagination is not changed by our own writes.
    account_ids = []
    cursor = None
    while True:
        params = {"user_ids": [ENTITY_ID], "toolkit_slugs": [toolkit]}
        if cursor:
            params["cursor"] = cursor
        page = client.connected_accounts.list(**params)
        account_ids.extend(account.id for account in page.items)
        cursor = getattr(page, "next_cursor", None)
        if not cursor:
            break
    for account_id in account_ids:
        client.connected_accounts.delete(nanoid=account_id)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

async def service_statuses(db: AsyncSession) -> list[IntegrationOut]:
    """Every supported provider and whether Dobby's service entity is really connected to it.

    Status comes from Composio, not the local table, so connections made outside the dashboard
    (or whose callback was lost) still show as connected and revoked ones don't. When Composio
    can't be reached the local records are returned with ``verified`` false.
    """
    result = await db.execute(select(Integration))
    records = {i.provider: i for i in result.scalars().all()}

    live = None
    if COMPOSIO_API_KEY:
        try:
            live = await asyncio.to_thread(_live_connections)
        except Exception as exc:
            logger.error("composio_status_failed type=%s", type(exc).__name__)

    out = []
    for provider, toolkit in SUPPORTED_PROVIDERS.items():
        record = records.get(provider)
        if live is None:
            state = "connected" if record else "disconnected"
            since = record.connected_at if record else None
        else:
            account = live.get(toolkit)
            if account is None:
                state = "disconnected"
            else:
                state = _LIVE_STATUS.get(account["raw"], "needs_reconnect")
            since = (record.connected_at if record else None) or (account or {}).get("created_at")
        out.append(IntegrationOut(
            provider=provider,
            status=state,
            verified=live is not None,
            connected_by=record.connected_by if record else None,
            connected_at=since if state != "disconnected" else None,
        ))
    return out


@router.get("", response_model=list[IntegrationOut])
async def list_integrations(
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(require_admin),
):
    """List every supported provider with its live connection status."""
    try:
        return await service_statuses(db)
    except Exception:
        logger.exception("Failed to list integrations")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Internal error")


@router.get("/{provider}/connect")
async def connect_integration(
    provider: str,
    request: Request,
    _admin: User = Depends(require_admin),
):
    """Initiate Composio OAuth flow for the given provider under the service entity."""
    if not COMPOSIO_API_KEY:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Composio is not configured",
        )

    toolkit = _get_toolkit(provider)

    try:
        redirect_url = await asyncio.to_thread(
            _authorize, toolkit, str(request.url_for("integration_callback", provider=provider))
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("composio_connect_failed provider=%s type=%s", provider, type(exc).__name__)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Could not reach Composio",
        )

    return RedirectResponse(url=redirect_url)


@router.get("/{provider}/callback", name="integration_callback")
async def integration_callback(
    provider: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """Record only a verified active connection belonging to Dobby's service identity."""
    toolkit = _get_toolkit(provider)
    # Failures land back on the integrations page with a banner, not a raw JSON error mid-OAuth.
    failed = RedirectResponse(url=f"{dashboard_url()}/dashboard/integrations?error={provider}")
    account_id = request.query_params.get("connected_account_id")
    if request.query_params.get("status") != "success" or not account_id:
        logger.warning("composio_connect_not_completed provider=%s", provider)
        return failed

    try:
        active = await asyncio.to_thread(_connection_is_active, toolkit, account_id)
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("composio_verify_failed provider=%s type=%s", provider, type(exc).__name__)
        return failed
    if not active:
        logger.warning("composio_connection_inactive provider=%s", provider)
        return failed

    try:
        result = await db.execute(
            select(Integration).where(Integration.provider == provider)
        )
        integration = result.scalar_one_or_none()

        if integration is None:
            integration = Integration(
                provider=provider,
                composio_entity_id=ENTITY_ID,
                connected_by=admin.id,
            )
        else:
            integration.composio_entity_id = ENTITY_ID
            integration.connected_by = admin.id
        db.add(integration)
        await db.commit()
    except Exception:
        logger.exception("Failed to store integration for provider %s", provider)
        return failed

    return RedirectResponse(url=f"{dashboard_url()}/dashboard/integrations?connected={provider}")


@router.delete("/{provider}", status_code=204)
async def disconnect_integration(
    provider: str,
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(require_admin),
):
    """Remove the service entity's provider connections before clearing the local record."""
    toolkit = _get_toolkit(provider)
    try:
        result = await db.execute(
            select(Integration).where(Integration.provider == provider)
        )
        integration = result.scalar_one_or_none()
        # Clean Composio even without a local row: a lost callback leaves live connections.
        try:
            await asyncio.to_thread(_disconnect_accounts, toolkit)
        except HTTPException:
            raise
        except Exception as exc:
            logger.error("composio_disconnect_failed provider=%s type=%s", provider, type(exc).__name__)
            raise HTTPException(status_code=502, detail="Could not disconnect Composio account") from None
        if integration is not None:
            await db.delete(integration)
            await db.commit()
    except HTTPException:
        raise
    except Exception:
        logger.exception("Failed to delete integration for provider %s", provider)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Internal error")
