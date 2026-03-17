"""Pydantic schemas for authentication endpoints."""

from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field


# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------

class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8)


class ParentLoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8)


class UserProfile(BaseModel):
    id: UUID
    email: str
    full_name: str
    phone: Optional[str] = None
    role: str
    is_active: bool
    is_verified: bool
    last_login: Optional[datetime] = None

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Password management
# ---------------------------------------------------------------------------

class ChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=8)
    new_password: str = Field(min_length=8)


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ResetPasswordRequest(BaseModel):
    new_password: str = Field(min_length=8)


# ---------------------------------------------------------------------------
# User management (admin)
# ---------------------------------------------------------------------------

class CreateUserRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8)
    full_name: str = Field(min_length=1)
    phone: Optional[str] = None
    role: str
    is_verified: bool = False


class UpdateUserRequest(BaseModel):
    full_name: Optional[str] = None
    phone: Optional[str] = None
    role: Optional[str] = None
    is_active: Optional[bool] = None
    is_verified: Optional[bool] = None


class UserListItem(BaseModel):
    id: UUID
    email: str
    full_name: str
    phone: Optional[str] = None
    role: str
    is_active: bool
    is_verified: bool
    last_login: Optional[datetime] = None
    created_at: datetime

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Roles & permissions (admin)
# ---------------------------------------------------------------------------

class RoleOut(BaseModel):
    id: UUID
    name: str
    description: Optional[str] = None
    is_system_role: bool
    user_count: int = 0

    model_config = {"from_attributes": True}


class PermissionOut(BaseModel):
    id: UUID
    resource: str
    action: str
    description: Optional[str] = None

    model_config = {"from_attributes": True}


class UpdateRolePermissionsRequest(BaseModel):
    permission_ids: list[UUID]


# ---------------------------------------------------------------------------
# Audit log
# ---------------------------------------------------------------------------

class AuditLogOut(BaseModel):
    id: UUID
    user_id: Optional[UUID] = None
    action: str
    resource_type: str
    resource_id: Optional[str] = None
    ip_address: Optional[str] = None
    user_agent: Optional[str] = None
    old_values: Optional[dict] = None
    new_values: Optional[dict] = None
    timestamp: datetime
    user_email: Optional[str] = None
    user_role: Optional[str] = None

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# System config
# ---------------------------------------------------------------------------

class SystemConfigOut(BaseModel):
    key: str
    masked_value: str  # last 4 chars only

    model_config = {"from_attributes": True}


class SystemConfigUpdate(BaseModel):
    key: str
    value: str


# ---------------------------------------------------------------------------
# Generic
# ---------------------------------------------------------------------------

class MessageResponse(BaseModel):
    message: str


class PaginatedResponse(BaseModel):
    items: list
    total: int
    page: int
    page_size: int
