import logging
import os

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
COMPOSIO_ENTITY_ID = os.environ.get("COMPOSIO_ENTITY_ID", "dobby")

PROVIDER_MAP = {
    "googlecalendar": "googlecalendar",
    "github": "github",
    "notion": "notion",
}


def _get_session():
    from composio import Composio
    client = Composio(api_key=COMPOSIO_API_KEY)
    return client.tool_router.create(
        user_id=COMPOSIO_ENTITY_ID,
        toolkits=list(PROVIDER_MAP.values()),
    )


@router.get("", response_model=list[IntegrationOut])
async def list_integrations(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        result = await db.execute(
            select(Integration)
            .where(Integration.user_id == current_user.id)
            .order_by(Integration.connected_at.desc())
        )
        integrations = result.scalars().all()
    except Exception:
        logger.exception("Failed to list integrations for user %s", current_user.id)
        raise HTTPException(status_code=500, detail="Internal error")

    return [IntegrationOut.model_validate(i) for i in integrations]


@router.get("/{provider}/connect")
async def connect_integration(
    provider: str,
    request: Request,
    current_user: User = Depends(get_current_user),
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
        callback = f"{DASHBOARD_URL}/api/integrations/{provider}/callback"
        conn_req = session.authorize(toolkit, callback_url=callback)
        redirect_url = getattr(conn_req, "redirect_url", None)

        if not redirect_url:
            conn_status = getattr(conn_req, "status", "")
            if conn_status.upper() in ("ACTIVE", "CONNECTED"):
                return RedirectResponse(url=f"{DASHBOARD_URL}/dashboard/integrations?connected={provider}")
            raise ValueError(f"No redirect URL from Composio (status={conn_status})")

    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Composio connect failed for %s: %s", provider, exc)
        raise HTTPException(status_code=502, detail=f"Composio error: {exc}")

    return RedirectResponse(url=redirect_url)


@router.get("/{provider}/callback")
async def integration_callback(
    provider: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if provider not in PROVIDER_MAP:
        raise HTTPException(status_code=400, detail=f"Unknown provider '{provider}'")

    try:
        result = await db.execute(
            select(Integration).where(
                Integration.user_id == current_user.id,
                Integration.provider == provider,
            )
        )
        existing = result.scalar_one_or_none()

        if existing is None:
            db.add(Integration(
                user_id=current_user.id,
                provider=provider,
                composio_entity_id=COMPOSIO_ENTITY_ID,
            ))
        else:
            existing.composio_entity_id = COMPOSIO_ENTITY_ID
        await db.commit()
    except Exception:
        logger.exception("Failed to store integration for %s %s", current_user.id, provider)
        raise HTTPException(status_code=500, detail="Internal error")

    return RedirectResponse(url=f"{DASHBOARD_URL}/dashboard/integrations?connected={provider}")


@router.delete("/{provider}", status_code=204)
async def disconnect_integration(
    provider: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        result = await db.execute(
            select(Integration).where(
                Integration.user_id == current_user.id,
                Integration.provider == provider,
            )
        )
        integration = result.scalar_one_or_none()
        if integration is None:
            raise HTTPException(status_code=404, detail=f"No '{provider}' integration found")
        await db.delete(integration)
        await db.commit()
    except HTTPException:
        raise
    except Exception:
        logger.exception("Failed to delete integration for %s %s", current_user.id, provider)
        raise HTTPException(status_code=500, detail="Internal error")
