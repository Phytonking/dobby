import logging
import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import Cookie, Depends, HTTPException, Request, status
from itsdangerous import URLSafeSerializer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .database import get_db
from .models import Session, User

logger = logging.getLogger(__name__)

SECRET_KEY = os.environ["SECRET_KEY"]
_s = URLSafeSerializer(SECRET_KEY, salt="session")
SESSION_DAYS = 7
COOKIE_NAME = "dobby_session"

# ---------------------------------------------------------------------------
# Token helpers
# ---------------------------------------------------------------------------

def make_token(user_id: str) -> str:
    """Create a signed token embedding the user_id plus random entropy."""
    return _s.dumps(user_id) + "." + secrets.token_hex(16)


def verify_token(token: str) -> Optional[str]:
    """Return user_id if the token signature is valid, otherwise None."""
    try:
        part = token.rsplit(".", 1)[0]
        return _s.loads(part)
    except Exception:
        return None


def session_expiry() -> datetime:
    return datetime.now(timezone.utc) + timedelta(days=SESSION_DAYS)


# ---------------------------------------------------------------------------
# Cookie helpers
# ---------------------------------------------------------------------------

def _is_secure() -> bool:
    return os.environ.get("ENV", "development") == "production"


def set_session_cookie(response, token: str) -> None:
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        httponly=True,
        samesite="lax",
        secure=_is_secure(),
        max_age=SESSION_DAYS * 86400,
        path="/",
    )


def clear_session_cookie(response) -> None:
    response.delete_cookie(key=COOKIE_NAME, path="/")


# ---------------------------------------------------------------------------
# Session DB helpers
# ---------------------------------------------------------------------------

async def create_session(
    db: AsyncSession,
    user_id,
    provider: str,
) -> str:
    """Insert a new session row and return the raw token."""
    from .models import Session  # local to avoid circular at module level

    token = make_token(str(user_id))
    expires_at = session_expiry()
    session = Session(
        user_id=user_id,
        token=token,
        provider=provider,
        expires_at=expires_at,
    )
    async with db.begin():
        db.add(session)
    return token


async def delete_session(db: AsyncSession, token: str) -> None:
    """Remove a session row by token."""
    result = await db.execute(select(Session).where(Session.token == token))
    session = result.scalar_one_or_none()
    if session:
        async with db.begin():
            await db.delete(session)


# ---------------------------------------------------------------------------
# FastAPI dependencies
# ---------------------------------------------------------------------------

async def get_current_user(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> User:
    """Read the session cookie, validate it against the DB, return the User."""
    token: Optional[str] = request.cookies.get(COOKIE_NAME)
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated"
        )

    user_id = verify_token(token)
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid session"
        )

    now = datetime.now(timezone.utc)
    result = await db.execute(
        select(Session)
        .where(Session.token == token)
        .where(Session.expires_at > now)
    )
    session = result.scalar_one_or_none()
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Session expired or not found"
        )

    user_result = await db.execute(select(User).where(User.id == session.user_id))
    user = user_result.scalar_one_or_none()
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found"
        )

    return user


async def require_admin(
    current_user: User = Depends(get_current_user),
) -> User:
    """Dependency that enforces admin role."""
    if current_user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required"
        )
    return current_user
