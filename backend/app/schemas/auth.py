"""Authentication and user-administration API contracts."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


SchoolRole = Literal["student", "professor", "school_admin"]


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: EmailStr
    password: str = Field(min_length=1, max_length=1024)


class MembershipCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    school_id: str = Field(min_length=1, max_length=30)
    role: SchoolRole


class MembershipResponse(BaseModel):
    id: UUID
    school_id: str
    role: SchoolRole
    is_active: bool


class UserSummary(BaseModel):
    id: UUID
    first_name: str | None
    last_name: str | None
    email: str
    is_active: bool
    global_role: Literal["super_admin"] | None
    must_change_password: bool
    created_at: datetime
    memberships: list[MembershipResponse]


class LoginResponse(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_at: datetime
    user: UserSummary


class ChangePasswordRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    current_password: str = Field(min_length=1, max_length=1024)
    new_password: str = Field(min_length=12, max_length=1024)


class UserCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    first_name: str = Field(min_length=1, max_length=100)
    last_name: str = Field(min_length=1, max_length=100)
    email: EmailStr
    temporary_password: str = Field(min_length=12, max_length=1024)
    global_role: Literal["super_admin"] | None = None
    memberships: list[MembershipCreate] = Field(default_factory=list)

    @field_validator("memberships")
    @classmethod
    def unique_memberships(cls, value: list[MembershipCreate]) -> list[MembershipCreate]:
        keys = {(item.school_id, item.role) for item in value}
        if len(keys) != len(value):
            raise ValueError("memberships must be unique")
        return value


class UserUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    first_name: str | None = Field(default=None, min_length=1, max_length=100)
    last_name: str | None = Field(default=None, min_length=1, max_length=100)
    email: EmailStr | None = None


class ResetPasswordRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    temporary_password: str = Field(min_length=12, max_length=1024)
