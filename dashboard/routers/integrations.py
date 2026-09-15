import logging
import os
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import get_current_user
from ..database import get_db
from ..models import Integration, User
from ..schemas import IntegrationOut

logger = logging.getLogger(__name__)
router = APIRouter()

DASHBOARD_URL = os.environ.get("DASHBOARD_URL", "http://localhost:3000")
COMPOSIO_API_KEY = os.environ.get("COMPOSIO_API_KEY", "")

# Supported providers mapped to Composio App enum values
SUPPORTED_PROVIDERS = {"github", "google_calendar", "notion"}


def _get_composio_app(provider: str):
    """Return the Composio App enum for a given provider string."""
    from composio import App  # import at call time so app still loads without composio

    mapping = {
        "github": App.GITHUB,
        "google_calendar": App.GOOGLECALENDAR,
        "notion": App.NOTION,
    }
    app = mapping.get(provider)
    if app is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported provider '{provider}'. Supported: {sorted(SUPPORTED_PROVIDERS)}",
        )
    return app


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("", response_model=list[IntegrationOut])
async def list_integrations(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List all connected integrations for the current user."""
    try:
        result = await db.execute(
            select(Integration)
            .where(Integration.user_id == current_user.id)
            .order_by(Integration.connected_at.desc())
        )
        integrations = result.scalars().all()
    except Exception:
        logger.exception("Failed to list integrations for user %s", current_user.id)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Internal error")

    return [IntegrationOut.model_validate(i) for i in integrations]


@router.get("/{provider}/connect")
async def connect_integration(
    provider: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Initiate Composio OAuth flow for the given provider."""
    if not COMPOSIO_API_KEY:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Composio is not configured",
        )

    composio_app = _get_composio_app(provider)

    try:
        from composio import ComposioToolSet

        toolset = ComposioToolSet(api_key=COMPOSIO_API_KEY)
        entity = toolset.get_entity(entity_id=str(current_user.id))
        connection_req = entity.initiate_connection(app=composio_app)
        redirect_url = connection_req.redirectUrl
    except HTTPException:
        raise
    except Exception:
        logger.exception(
            "Failed to initiate Composio connection for user %s provider %s",
            current_user.id,
            provider,
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Could not reach Composio",
        )

    return RedirectResponse(url=redirect_url)


@router.get("/{provider}/callback")
async def integration_callback(
    provider: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Handle Composio OAuth callback — store or update the entity_id row."""
    if provider not in SUPPORTED_PROVIDERS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported provider '{provider}'",
        )

    # Composio uses the entity_id we set (str(user.id)) — store it in the DB
    entity_id = str(current_user.id)

    try:
        result = await db.execute(
            select(Integration).where(
                Integration.user_id == current_user.id,
                Integration.provider == provider,
            )
        )
        integration = result.scalar_one_or_none()

        async with db.begin():
            if integration is None:
                integration = Integration(
                    user_id=current_user.id,
                    provider=provider,
                    composio_entity_id=entity_id,
                )
                db.add(integration)
            else:
                integration.composio_entity_id = entity_id
                db.add(integration)
    except Exception:
        logger.exception(
            "Failed to store integration for user %s provider %s",
            current_user.id,
            provider,
        )
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Internal error")

    return RedirectResponse(url=f"{DASHBOARD_URL}/dashboard/integrations")


@router.delete("/{provider}", status_code=204)
async def disconnect_integration(
    provider: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Remove a Composio integration for the current user."""
    try:
        result = await db.execute(
            select(Integration).where(
                Integration.user_id == current_user.id,
                Integration.provider == provider,
            )
        )
        integration = result.scalar_one_or_none()
        if integration is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"No '{provider}' integration found",
            )
        async with db.begin():
            await db.delete(integration)
    except HTTPException:
        raise
    except Exception:
        logger.exception(
            "Failed to delete integration for user %s provider %s",
            current_user.id,
            provider,
        )
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Internal error")
