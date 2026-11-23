import csv
from datetime import datetime, timedelta, timezone
import io
import logging
from typing import Optional
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import ValidationError
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import require_admin
from ..database import get_db
from ..models import AgentAction, User
from .integrations import SUPPORTED_PROVIDERS, service_statuses
from ..schemas import (
    AuditLogEntry,
    HealthBucket,
    HealthReport,
    ImportResult,
    ImportRow,
    ServiceHealth,
    UserImport,
    PaginatedAudit,
    PaginatedUsers,
    UserCreate,
    UserOut,
    UserRoleUpdate,
    UserUpdate,
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
    if body.role not in ("admin", "student"):
        raise HTTPException(status_code=422, detail="role must be 'admin' or 'student'")
    if not body.uw_email and not body.discord_id:
        raise HTTPException(
            status_code=422,
            detail="Add a Google email or a Discord ID so this person can be recognised",
        )
    try:
        if body.uw_email:
            existing = await db.execute(
                select(User).where(func.lower(User.uw_email) == body.uw_email)
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
            calendar_email=body.calendar_email,
            role=body.role,
            added_by=admin.id,
        )
        db.add(user)
        await db.commit()
    except HTTPException:
        raise
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=409, detail="A user with that email or Discord ID already exists") from None
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
        await db.delete(user)
        await db.commit()
    except HTTPException:
        raise
    except Exception:
        logger.exception("Failed to delete user %s", user_id)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Internal error")


@router.patch("/users/{user_id}", response_model=UserOut)
async def update_user(
    user_id: uuid.UUID,
    body: UserUpdate,
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(require_admin),
):
    update_data = body.model_dump(exclude_unset=True)
    if not update_data:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Nothing to update",
        )
    try:
        result = await db.execute(select(User).where(User.id == user_id))
        user = result.scalar_one_or_none()
        if user is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
        for field, value in update_data.items():
            setattr(user, field, value)
        db.add(user)
        await db.commit()
    except HTTPException:
        raise
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=409, detail="Another user already has that Discord ID") from None
    except Exception:
        logger.exception("Failed to update user %s", user_id)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Internal error")

    return UserOut.model_validate(user)


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
        user.role = body.role
        db.add(user)
        await db.commit()
    except HTTPException:
        raise
    except Exception:
        logger.exception("Failed to update role for user %s", user_id)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Internal error")

    return UserOut.model_validate(user)


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

    return PaginatedAudit(total=total, items=[AuditLogEntry.model_validate(r) for r in rows])


# ---------------------------------------------------------------------------
# Bulk import
# ---------------------------------------------------------------------------

MAX_IMPORT_BYTES = 256 * 1024
MAX_IMPORT_ROWS = 500

# Header spellings we accept, including Google Contacts' export ("E-mail 1 - Value").
_HEADERS = {
    "display_name": {"name", "display name", "display_name", "full name"},
    "first_name": {"first name", "first_name", "given name"},
    "last_name": {"last name", "last_name", "family name"},
    "uw_email": {"email", "e-mail", "email address", "google email", "google_email", "uw_email",
                 "e-mail 1 - value"},
    "discord_id": {"discord", "discord id", "discord_id", "discord user id"},
    "calendar_email": {"calendar email", "calendar_email", "calendar"},
    "role": {"role"},
}


def _columns(fieldnames: list[str]) -> dict[str, str]:
    """Map our field names to the CSV's own header text."""
    found = {}
    for header in fieldnames:
        key = (header or "").strip().lower()
        for field, names in _HEADERS.items():
            if key in names and field not in found:
                found[field] = header
    return found


def _parse_import(text: str) -> tuple[list[tuple[int, dict]], dict[str, str]]:
    if len(text.encode()) > MAX_IMPORT_BYTES:
        raise HTTPException(status_code=413, detail="CSV is larger than 256 KB; split it up")
    reader = csv.DictReader(io.StringIO(text.lstrip("\ufeff")))
    cols = _columns(reader.fieldnames or [])
    has_name = "display_name" in cols or "first_name" in cols
    if not has_name or not ({"uw_email", "discord_id"} & cols.keys()):
        raise HTTPException(
            status_code=422,
            detail="The CSV needs a name column and an email or Discord ID column "
                   "(e.g. name,email,discord_id,calendar_email,role)",
        )
    rows = []
    for line, raw in enumerate(reader, start=2):
        if line - 1 > MAX_IMPORT_ROWS:
            raise HTTPException(status_code=413, detail=f"At most {MAX_IMPORT_ROWS} people per import")

        def get(field):
            value = raw.get(cols[field]) if field in cols else None
            return (value or "").strip() or None

        name = get("display_name") or " ".join(filter(None, [get("first_name"), get("last_name")])) or None
        if not any(get(f) for f in cols) and not name:
            continue  # blank line
        rows.append((line, {
            "display_name": name or "",
            "uw_email": get("uw_email"),
            "discord_id": get("discord_id"),
            # Importing contacts is about who Dobby can invite: default to their email.
            "calendar_email": get("calendar_email") or get("uw_email"),
            "role": (get("role") or "student").lower(),
        }))
    return rows, cols


def _first_error(exc: ValidationError) -> str:
    err = exc.errors()[0]
    field = ".".join(str(p) for p in err.get("loc", ())).replace("uw_email", "email").replace("_", " ")
    msg = err.get("msg", "is invalid").removeprefix("Value error, ")
    return f"{field}: {msg}" if field else msg


@router.post("/users/import", response_model=ImportResult)
async def import_users(
    body: UserImport,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """Add many teammates from a CSV at once. Existing people are skipped, never overwritten."""
    parsed, _cols = _parse_import(body.csv)

    report: list[ImportRow] = []
    valid: list[tuple[int, UserCreate]] = []
    for line, data in parsed:
        try:
            person = UserCreate(**data)
        except ValidationError as exc:
            report.append(ImportRow(line=line, display_name=data["display_name"] or None,
                                    outcome="invalid", reason=_first_error(exc)))
            continue
        if person.role not in ("admin", "student"):
            report.append(ImportRow(line=line, display_name=person.display_name, outcome="invalid",
                                    reason="role: must be admin or student"))
            continue
        if not person.uw_email and not person.discord_id:
            report.append(ImportRow(line=line, display_name=person.display_name, outcome="invalid",
                                    reason="needs an email or a Discord ID"))
            continue
        valid.append((line, person))

    emails = {p.uw_email for _, p in valid if p.uw_email}
    ids = {p.discord_id for _, p in valid if p.discord_id}
    try:
        conditions = []
        if emails:
            conditions.append(func.lower(User.uw_email).in_(emails))
        if ids:
            conditions.append(User.discord_id.in_(ids))
        existing = []
        if conditions:
            existing = (await db.execute(select(User).where(or_(*conditions)))).scalars().all()
    except Exception:
        logger.exception("Failed to check existing users for import")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Internal error")

    taken_emails = {(u.uw_email or "").lower() for u in existing if u.uw_email}
    taken_ids = {u.discord_id for u in existing if u.discord_id}
    seen_emails: set[str] = set()
    seen_ids: set[str] = set()
    to_create: list[User] = []
    for line, person in valid:
        if (person.uw_email and person.uw_email in taken_emails) or (
            person.discord_id and person.discord_id in taken_ids
        ):
            report.append(ImportRow(line=line, display_name=person.display_name, outcome="skipped",
                                    reason="already on the roster"))
            continue
        if (person.uw_email and person.uw_email in seen_emails) or (
            person.discord_id and person.discord_id in seen_ids
        ):
            report.append(ImportRow(line=line, display_name=person.display_name, outcome="skipped",
                                    reason="listed twice in this file"))
            continue
        if person.uw_email:
            seen_emails.add(person.uw_email)
        if person.discord_id:
            seen_ids.add(person.discord_id)
        report.append(ImportRow(line=line, display_name=person.display_name,
                                outcome="would_create" if body.dry_run else "created"))
        to_create.append(User(
            uw_email=person.uw_email,
            discord_id=person.discord_id,
            display_name=person.display_name,
            calendar_email=person.calendar_email,
            role=person.role,
            added_by=admin.id,
        ))

    if to_create and not body.dry_run:
        try:
            for user in to_create:
                db.add(user)
            await db.commit()
        except IntegrityError:
            await db.rollback()
            raise HTTPException(
                status_code=409,
                detail="Someone in this file was added while you were importing; preview again",
            ) from None
        except Exception:
            logger.exception("Failed to import users")
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Internal error")
        logger.info("users_imported count=%d by=%s", len(to_create), admin.id)

    report.sort(key=lambda r: r.line)
    return ImportResult(
        created=len(to_create),
        skipped=sum(r.outcome == "skipped" for r in report),
        invalid=sum(r.outcome == "invalid" for r in report),
        rows=report,
    )


# ---------------------------------------------------------------------------
# System health
# ---------------------------------------------------------------------------

HEALTH_WINDOW_HOURS = 24


def _now() -> datetime:
    return datetime.now(timezone.utc)

# Tool names come in two shapes: Composio slugs (GOOGLECALENDAR_CREATE_EVENT) and Dobby's own
# dotted names (google_calendar.create). Both start with the service.
_TOOL_PREFIXES = {
    "google_calendar": ("googlecalendar_", "google_calendar"),
    "notion": ("notion_", "notion."),
    "instagram": ("instagram_", "instagram."),
    "linkedin": ("linkedin_", "linkedin."),
}


def service_for_tool(tool: Optional[str]) -> str:
    name = (tool or "").lower()
    for key, prefixes in _TOOL_PREFIXES.items():
        if name.startswith(prefixes):
            return key
    return "dobby"


@router.get("/health", response_model=HealthReport)
async def system_health(
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(require_admin),
):
    """The last 24 hours at a glance: live connections plus tool calls and failures per service."""
    now = _now()
    since = now - timedelta(hours=HEALTH_WINDOW_HOURS)
    try:
        connections = await service_statuses(db)
        rows = (await db.execute(
            select(AgentAction)
            .where(AgentAction.created_at >= since)
            .order_by(AgentAction.created_at.desc())
            .limit(50_000)
        )).scalars().all()
    except Exception:
        logger.exception("Failed to build health report")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Internal error")

    keys = [*SUPPORTED_PROVIDERS, "dobby"]
    stats = {k: ServiceHealth(key=k, calls=0, failures=0) for k in keys}
    for c in connections:
        if c.provider in stats:
            stats[c.provider].connection = c.status
    first_hour = since.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    hourly = [HealthBucket(hour=first_hour + timedelta(hours=i), ok=0, failed=0)
              for i in range(HEALTH_WINDOW_HOURS)]

    for row in rows:
        when = row.created_at
        if when is None:
            continue
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        failed = (row.status or "") != "ok"
        entry = stats[service_for_tool(row.tool)]
        entry.calls += 1
        if failed:
            entry.failures += 1
            entry.last_failure_at = max(filter(None, [entry.last_failure_at, when]))
        else:
            entry.last_success_at = max(filter(None, [entry.last_success_at, when]))
        index = int((when - first_hour).total_seconds() // 3600)
        index = min(max(index, 0), HEALTH_WINDOW_HOURS - 1)
        if failed:
            hourly[index].failed += 1
        else:
            hourly[index].ok += 1

    times = [r.created_at for r in rows if r.created_at is not None]
    return HealthReport(
        window_hours=HEALTH_WINDOW_HOURS,
        generated_at=now,
        composio_verified=bool(connections) and connections[0].verified,
        calls=sum(s.calls for s in stats.values()),
        failures=sum(s.failures for s in stats.values()),
        last_activity_at=max(times) if times else None,
        services=list(stats.values()),
        hourly=hourly,
    )
