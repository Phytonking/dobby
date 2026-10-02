from datetime import datetime
import re
from typing import Optional
import uuid

from pydantic import BaseModel, ConfigDict, field_validator

# Same shape the bot accepts for /email; keeps the two entry points in step.
EMAIL = re.compile(r"^[^@\s<>]+@[^@\s<>]+\.[^@\s<>]+$")


def clean_email(value: Optional[str]) -> Optional[str]:
    """Trim and lowercase; empty clears the address; anything malformed is rejected."""
    if value is None:
        return None
    value = value.strip().strip("<>").lower()
    if not value:
        return None
    if len(value) > 254 or EMAIL.match(value) is None:
        raise ValueError("must be a valid email address")
    return value


# ---------------------------------------------------------------------------
# User schemas
# ---------------------------------------------------------------------------

class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    uw_email: Optional[str] = None
    discord_id: Optional[str] = None
    display_name: Optional[str] = None
    calendar_email: Optional[str] = None
    role: str
    created_at: Optional[datetime] = None


class UserCreate(BaseModel):
    uw_email: str
    discord_id: Optional[str] = None
    display_name: str
    calendar_email: Optional[str] = None
    role: str = "student"

    _clean = field_validator("calendar_email")(clean_email)


class UserUpdate(BaseModel):
    """Admin edit; only the fields sent are changed."""

    display_name: Optional[str] = None
    discord_id: Optional[str] = None
    calendar_email: Optional[str] = None

    _clean = field_validator("calendar_email")(clean_email)


class MeUpdate(BaseModel):
    """What a signed-in user may change about themselves."""

    calendar_email: Optional[str] = None

    _clean = field_validator("calendar_email")(clean_email)


class UserRoleUpdate(BaseModel):
    role: str  # "admin" | "student"


# ---------------------------------------------------------------------------
# Session / auth schemas
# ---------------------------------------------------------------------------

class SessionUser(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    display_name: Optional[str] = None
    role: str
    uw_email: Optional[str] = None
    discord_id: Optional[str] = None
    calendar_email: Optional[str] = None


# ---------------------------------------------------------------------------
# Integration schemas
# ---------------------------------------------------------------------------

class IntegrationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    provider: str
    connected_by: Optional[uuid.UUID] = None
    connected_at: Optional[datetime] = None


# ---------------------------------------------------------------------------
# Audit log schemas
# ---------------------------------------------------------------------------

class AuditLogEntry(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    discord_id: Optional[str] = None
    tool: Optional[str] = None
    status: Optional[str] = None
    duration_ms: Optional[int] = None
    created_at: Optional[datetime] = None


# ---------------------------------------------------------------------------
# Generic paginated wrapper
# ---------------------------------------------------------------------------

class PaginatedUsers(BaseModel):
    total: int
    items: list[UserOut]


class PaginatedAudit(BaseModel):
    total: int
    items: list[AuditLogEntry]
