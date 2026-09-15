import logging
import os
from typing import Optional

import httpx
from authlib.integrations.starlette_client import OAuth
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import (
    COOKIE_NAME,
    clear_session_cookie,
    create_session,
    delete_session,
    get_current_user,
    set_session_cookie,
)
from ..database import get_db
from ..models import User

logger = logging.getLogger(__name__)
router = APIRouter()

# ---------------------------------------------------------------------------
# OAuth client setup
# ---------------------------------------------------------------------------

oauth = OAuth()

oauth.register(
    name="google",
    client_id=os.environ.get("GOOGLE_CLIENT_ID"),
    client_secret=os.environ.get("GOOGLE_CLIENT_SECRET"),
    server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
    client_kwargs={
        "scope": "openid email profile",
        "hd": "uw.edu",
    },
)

oauth.register(
    name="discord",
    client_id=os.environ.get("DISCORD_CLIENT_ID"),
    client_secret=os.environ.get("DISCORD_CLIENT_SECRET"),
    authorize_url="https://discord.com/api/oauth2/authorize",
    access_token_url="https://discord.com/api/oauth2/token",
    client_kwargs={"scope": "identify email"},
)

DASHBOARD_URL = os.environ.get("DASHBOARD_URL", "http://localhost:3000")


# ---------------------------------------------------------------------------
# Google OAuth
# ---------------------------------------------------------------------------

@router.get("/google")
async def auth_google(request: Request):
    redirect_uri = str(request.url_for("auth_google_callback"))
    return await oauth.google.authorize_redirect(
        request,
        redirect_uri,
        hd="uw.edu",
    )


@router.get("/google/callback", name="auth_google_callback")
async def auth_google_callback(request: Request, db: AsyncSession = Depends(get_db)):
    try:
        token = await oauth.google.authorize_access_token(request)
    except Exception:
        logger.exception("Google OAuth token exchange failed")
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="OAuth failed")

    userinfo = token.get("userinfo") or {}
    email: Optional[str] = userinfo.get("email")
    hd: Optional[str] = userinfo.get("hd")

    if not email or hd != "uw.edu":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access restricted to UW accounts",
        )

    try:
        result = await db.execute(select(User).where(User.uw_email == email))
        user = result.scalar_one_or_none()
    except Exception:
        logger.exception("DB error during Google callback lookup")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Internal error")

    if user is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account not pre-registered. Contact an admin.",
        )

    try:
        token_str = await create_session(db, user.id, provider="google")
    except Exception:
        logger.exception("Failed to create session for user %s", user.id)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Internal error")

    response = RedirectResponse(url=f"{DASHBOARD_URL}/dashboard")
    set_session_cookie(response, token_str)
    return response


# ---------------------------------------------------------------------------
# Discord OAuth
# ---------------------------------------------------------------------------

@router.get("/discord")
async def auth_discord(request: Request):
    redirect_uri = str(request.url_for("auth_discord_callback"))
    return await oauth.discord.authorize_redirect(request, redirect_uri)


@router.get("/discord/callback", name="auth_discord_callback")
async def auth_discord_callback(request: Request, db: AsyncSession = Depends(get_db)):
    try:
        token = await oauth.discord.authorize_access_token(request)
    except Exception:
        logger.exception("Discord OAuth token exchange failed")
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="OAuth failed")

    access_token = token.get("access_token")
    if not access_token:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No access token from Discord")

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                "https://discord.com/api/users/@me",
                headers={"Authorization": f"Bearer {access_token}"},
                timeout=10,
            )
            resp.raise_for_status()
            discord_user = resp.json()
    except Exception:
        logger.exception("Failed to fetch Discord user info")
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Could not reach Discord API")

    discord_id: Optional[str] = discord_user.get("id")
    if not discord_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Discord ID not returned")

    try:
        result = await db.execute(select(User).where(User.discord_id == discord_id))
        user = result.scalar_one_or_none()
    except Exception:
        logger.exception("DB error during Discord callback lookup")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Internal error")

    if user is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Discord account not pre-registered. Contact an admin.",
        )

    try:
        token_str = await create_session(db, user.id, provider="discord")
    except Exception:
        logger.exception("Failed to create session for user %s", user.id)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Internal error")

    response = RedirectResponse(url=f"{DASHBOARD_URL}/dashboard")
    set_session_cookie(response, token_str)
    return response


# ---------------------------------------------------------------------------
# Logout
# ---------------------------------------------------------------------------

@router.post("/logout")
async def logout(request: Request, db: AsyncSession = Depends(get_db)):
    token: Optional[str] = request.cookies.get(COOKIE_NAME)
    if token:
        try:
            await delete_session(db, token)
        except Exception:
            logger.exception("Error deleting session during logout")
            # proceed anyway — clear the cookie regardless

    response = RedirectResponse(url=f"{DASHBOARD_URL}/login", status_code=302)
    clear_session_cookie(response)
    return response
