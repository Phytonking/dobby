import logging
import os
import time
from collections import deque
from typing import Optional

import httpx
from authlib.integrations.starlette_client import OAuth
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import RedirectResponse, JSONResponse, Response
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import (
    COOKIE_NAME,
    clear_session_cookie,
    create_session,
    delete_session,
    set_session_cookie,
)
from ..database import get_db
from ..models import User
from ..login_config import auth_mode, dashboard_url, dashboard_urls, require_provider
from ..passwords import hash_password, verify_password

logger = logging.getLogger(__name__)
router = APIRouter()

# Per-process limiter: the shipped dashboard runs a single worker. Bound memory
# and reject excess callers rather than evicting active limits.
_attempts: dict[str, deque] = {}
_dummy_hash = hash_password("dummy-password-for-timing-only")


class LocalLogin(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


@router.get("/methods")
async def login_methods():
    mode = auth_mode()
    return {"local": mode in {"local", "both"}, "oauth": mode in {"oauth", "both"}}


@router.post("/local")
async def local_login(body: LocalLogin, request: Request, db: AsyncSession = Depends(get_db)):
    require_provider("local", request)
    origin = request.headers.get("origin")
    if origin is not None and origin.rstrip("/") not in dashboard_urls():
        raise HTTPException(403, "Invalid login origin")
    now = time.monotonic()
    for host in list(_attempts):
        while _attempts[host] and _attempts[host][0] <= now - 60:
            _attempts[host].popleft()
        if not _attempts[host]:
            del _attempts[host]
    host = request.client.host
    if (host not in _attempts and len(_attempts) >= 4096) or len(_attempts.get(host, ())) >= 5:
        raise HTTPException(429, "Too many login attempts. Try again in a minute.", headers={"Retry-After": "60"})
    _attempts.setdefault(host, deque()).append(now)
    user = (await db.execute(select(User).where(
        User.local_username == body.username.strip().lower()
    ))).scalar_one_or_none()
    valid = await run_in_threadpool(
        verify_password, body.password, user.password_hash if user and user.password_hash else _dummy_hash
    )
    if not valid or user is None or not user.password_hash:
        raise HTTPException(401, "Invalid username or password")
    token = await create_session(db, user.id, provider="local")
    response = JSONResponse({"ok": True})
    set_session_cookie(response, token)
    return response

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

# ---------------------------------------------------------------------------
# Google OAuth
# ---------------------------------------------------------------------------

@router.get("/google")
async def auth_google(request: Request):
    require_provider("google", request)
    redirect_uri = str(request.url_for("auth_google_callback"))
    return await oauth.google.authorize_redirect(
        request,
        redirect_uri,
    )


@router.get("/google/callback", name="auth_google_callback")
async def auth_google_callback(request: Request, db: AsyncSession = Depends(get_db)):
    require_provider("google", request)
    try:
        token = await oauth.google.authorize_access_token(request)
    except Exception:
        logger.exception("Google OAuth token exchange failed")
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="OAuth failed")

    userinfo = token.get("userinfo") or {}
    email: Optional[str] = userinfo.get("email")
    if not email or userinfo.get("email_verified") is not True:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="A verified Google email address is required",
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

    response = RedirectResponse(url=f"{dashboard_url()}/dashboard")
    set_session_cookie(response, token_str)
    return response


# ---------------------------------------------------------------------------
# Discord OAuth
# ---------------------------------------------------------------------------

@router.get("/discord")
async def auth_discord(request: Request):
    require_provider("discord", request)
    redirect_uri = str(request.url_for("auth_discord_callback"))
    return await oauth.discord.authorize_redirect(request, redirect_uri)


@router.get("/discord/callback", name="auth_discord_callback")
async def auth_discord_callback(request: Request, db: AsyncSession = Depends(get_db)):
    require_provider("discord", request)
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

    response = RedirectResponse(url=f"{dashboard_url()}/dashboard")
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

    response = Response(status_code=204)
    clear_session_cookie(response)
    return response
