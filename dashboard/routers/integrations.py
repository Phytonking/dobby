"""Dobby's service accounts.

One Composio entity (COMPOSIO_ENTITY_ID) owns the Google Calendar, Notion, Instagram and
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

@router.get("", response_model=list[IntegrationOut])
async def list_integrations(
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(require_admin),
):
    """List the providers Dobby's service entity is connected to."""
    try:
        result = await db.execute(
            select(Integration).order_by(Integration.connected_at.desc())
        )
        integrations = result.scalars().all()
    except Exception:
        logger.exception("Failed to list integrations")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Internal error")

    return [IntegrationOut.model_validate(i) for i in integrations]


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
