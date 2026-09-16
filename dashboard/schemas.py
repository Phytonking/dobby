from datetime import datetime
from typing import Optional
import uuid

from pydantic import BaseModel, ConfigDict


# ---------------------------------------------------------------------------
# User schemas
# ---------------------------------------------------------------------------

class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    uw_email: Optional[str] = None
    discord_id: Optional[str] = None
    display_name: Optional[str] = None
    role: str
    created_at: Optional[datetime] = None


class UserCreate(BaseModel):
    uw_email: str
    discord_id: Optional[str] = None
    display_name: str
    role: str = "student"


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


# ---------------------------------------------------------------------------
# Guild settings schemas
# ---------------------------------------------------------------------------

class GuildSettingsOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    guild_id: str
    timezone: Optional[str] = None
    model: Optional[str] = None
    allowed_role_ids: Optional[list[str]] = None
    admin_role_ids: Optional[list[str]] = None
    allowed_channel_ids: Optional[list[str]] = None
    mention_channel_ids: Optional[list[str]] = None
    context_limit: Optional[int] = None
    updated_at: Optional[datetime] = None


class GuildSettingsUpdate(BaseModel):
    timezone: Optional[str] = None
    model: Optional[str] = None
    allowed_role_ids: Optional[list[str]] = None
    admin_role_ids: Optional[list[str]] = None
    allowed_channel_ids: Optional[list[str]] = None
    mention_channel_ids: Optional[list[str]] = None
    context_limit: Optional[int] = None


# ---------------------------------------------------------------------------
# Integration schemas
# ---------------------------------------------------------------------------

class IntegrationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    provider: str
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
    input_summary: Optional[str] = None  # derived field, populated manually


# ---------------------------------------------------------------------------
# Contact schemas
# ---------------------------------------------------------------------------

class ContactOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    name_key: Optional[str] = None
    display_name: Optional[str] = None
    email: Optional[str] = None
    added_by: Optional[str] = None
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
