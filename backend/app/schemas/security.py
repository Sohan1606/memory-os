"""V8.5 authentication / administration request schemas.

Strict models: every field is explicitly declared and bounded, so there is no
mass-assignment surface (a caller cannot smuggle `role`, `tenant_id` or
`namespace` into requests that do not declare them).
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class RegisterRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=10, max_length=256)
    display_name: str = Field(default="", max_length=120)


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=256)


class UserCreateRequest(BaseModel):
    """Admin-created account inside the admin's OWN tenant. `tenant_id` and
    `namespace` are deliberately not accepted — they are server-assigned."""
    model_config = ConfigDict(extra="forbid")
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=10, max_length=256)
    display_name: str = Field(default="", max_length=120)
    role: str = Field(default="member", pattern="^(member|admin|owner)$")


class RoleChangeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: str = Field(pattern="^(member|admin|owner)$")


class NamespaceMigrationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    legacy_namespace: str | None = Field(default=None, max_length=100)
