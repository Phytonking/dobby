"""Local username/password auth — only active when AUTH_MODE=local or both."""

import logging
import os

import bcrypt
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import create_session, set_session_cookie
from ..database import get_db
from ..models import User

logger = logging.getLogger(__name__)
router = APIRouter()

DASHBOARD_URL = os.environ.get("DASHBOARD_URL", "http://localhost:3000")


class LocalLoginBody(BaseModel):
    username: str
    password: str


@router.post("/local")
async def local_login(body: LocalLoginBody, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(User).where(
            (User.uw_email == body.username) | (User.display_name == body.username)
        )
    )
    user = result.scalar_one_or_none()

    if user is None or not user.password_hash:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    if not bcrypt.checkpw(body.password.encode(), user.password_hash.encode()):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    token = await create_session(db, user.id, provider="local")
    from fastapi.responses import JSONResponse
    response = JSONResponse(content={"ok": True, "redirect": "/dashboard"})
    set_session_cookie(response, token)
    return response
