import logging
from typing import Optional
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import require_admin
from ..database import get_db
from ..models import AgentAction, Contact, GuildSettings, User
from ..schemas import (
    AuditLogEntry,
    ContactOut,
    GuildSettingsOut,
    GuildSettingsUpdate,
    PaginatedAudit,
    PaginatedUsers,
    UserCreate,
    UserOut,
    UserRoleUpdate,
)

logger = logging.getLogger(__name__)
router = APIRouter()


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------

@router.get("/users", response_model=PaginatedUsers)
async def list_users(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(require_admin),
):
    try:
        total_result = await db.execute(select(func.count()).select_from(User))
        total = total_result.scalar_one()
        result = await db.execute(
            select(User).order_by(User.created_at.desc()).limit(limit).offset(offset)
        )
        users = result.scalars().all()
    except Exception:
        logger.exception("Failed to list users")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Internal error")

    return PaginatedUsers(
        total=total,
        items=[UserOut.model_validate(u) for u in users],
    )


@router.post("/users", response_model=UserOut, status_code=201)
async def create_user(
    body: UserCreate,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    # Check uniqueness
    try:
        existing = await db.execute(
            select(User).where(User.uw_email == body.uw_email)
        )
        if existing.scalar_one_or_none() is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="A user with that email already exists",
            )

        user = User(
            uw_email=body.uw_email,
            discord_id=body.discord_id,
            display_name=body.display_name,
            role=body.role,
            added_by=admin.id,
        )
        async with db.begin():
            db.add(user)
    except HTTPException:
        raise
    except Exception:
        logger.exception("Failed to create user")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Internal error")

    return UserOut.model_validate(user)


@router.delete("/users/{user_id}", status_code=204)
async def delete_user(
    user_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    if user_id == admin.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You cannot remove yourself",
        )
    try:
        result = await db.execute(select(User).where(User.id == user_id))
        user = result.scalar_one_or_none()
        if user is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
        async with db.begin():
            await db.delete(user)
    except HTTPException:
        raise
    except Exception:
        logger.exception("Failed to delete user %s", user_id)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Internal error")


@router.patch("/users/{user_id}/role", response_model=UserOut)
async def update_user_role(
    user_id: uuid.UUID,
    body: UserRoleUpdate,
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(require_admin),
):
    if body.role not in ("admin", "student"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="role must be 'admin' or 'student'",
        )
    try:
        result = await db.execute(select(User).where(User.id == user_id))
        user = result.scalar_one_or_none()
        if user is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
        async with db.begin():
            user.role = body.role
            db.add(user)
    except HTTPException:
        raise
    except Exception:
        logger.exception("Failed to update role for user %s", user_id)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Internal error")

    return UserOut.model_validate(user)


# ---------------------------------------------------------------------------
# Guild settings
# ---------------------------------------------------------------------------

@router.get("/settings/{guild_id}", response_model=GuildSettingsOut)
async def get_guild_settings(
    guild_id: str,
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(require_admin),
):
    try:
        result = await db.execute(
            select(GuildSettings).where(GuildSettings.guild_id == guild_id)
        )
        settings = result.scalar_one_or_none()
    except Exception:
        logger.exception("Failed to fetch guild settings for %s", guild_id)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Internal error")

    if settings is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Guild settings not found")

    return GuildSettingsOut.model_validate(settings)


@router.put("/settings/{guild_id}", response_model=GuildSettingsOut)
async def upsert_guild_settings(
    guild_id: str,
    body: GuildSettingsUpdate,
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(require_admin),
):
    try:
        result = await db.execute(
            select(GuildSettings).where(GuildSettings.guild_id == guild_id)
        )
        settings = result.scalar_one_or_none()

        update_data = body.model_dump(exclude_unset=True)

        async with db.begin():
            if settings is None:
                settings = GuildSettings(guild_id=guild_id, **update_data)
                db.add(settings)
            else:
                for field, value in update_data.items():
                    setattr(settings, field, value)
                db.add(settings)
    except Exception:
        logger.exception("Failed to upsert guild settings for %s", guild_id)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Internal error")

    return GuildSettingsOut.model_validate(settings)


# ---------------------------------------------------------------------------
# Audit log
# ---------------------------------------------------------------------------

@router.get("/audit", response_model=PaginatedAudit)
async def list_audit(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    tool: Optional[str] = Query(None),
    status_filter: Optional[str] = Query(None, alias="status"),
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(require_admin),
):
    try:
        base_q = select(AgentAction)
        count_q = select(func.count()).select_from(AgentAction)

        if tool:
            base_q = base_q.where(AgentAction.tool == tool)
            count_q = count_q.where(AgentAction.tool == tool)
        if status_filter:
            base_q = base_q.where(AgentAction.status == status_filter)
            count_q = count_q.where(AgentAction.status == status_filter)

        total_result = await db.execute(count_q)
        total = total_result.scalar_one()

        result = await db.execute(
            base_q.order_by(AgentAction.created_at.desc()).limit(limit).offset(offset)
        )
        rows = result.scalars().all()
    except Exception:
        logger.exception("Failed to fetch audit log")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Internal error")

    items = []
    for row in rows:
        # Produce a brief summary of the input without exposing raw data wholesale
        input_summary: Optional[str] = None
        if row.input and isinstance(row.input, dict):
            keys = list(row.input.keys())[:5]
            input_summary = ", ".join(keys) if keys else None

        items.append(
            AuditLogEntry(
                id=row.id,
                discord_id=row.discord_id,
                tool=row.tool,
                status=row.status,
                duration_ms=row.duration_ms,
                created_at=row.created_at,
                input_summary=input_summary,
            )
        )

    return PaginatedAudit(total=total, items=items)


# ---------------------------------------------------------------------------
# Contacts
# ---------------------------------------------------------------------------

@router.get("/contacts/{guild_id}", response_model=list[ContactOut])
async def list_contacts(
    guild_id: str,
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(require_admin),
):
    try:
        result = await db.execute(
            select(Contact)
            .where(Contact.guild_id == guild_id)
            .order_by(Contact.created_at.desc())
        )
        contacts = result.scalars().all()
    except Exception:
        logger.exception("Failed to fetch contacts for guild %s", guild_id)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Internal error")

    return [ContactOut.model_validate(c) for c in contacts]
