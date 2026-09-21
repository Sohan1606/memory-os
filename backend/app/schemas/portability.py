"""Validated HTTP inputs for the V8.4.4 portability namespace."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator


class ExportCreateRequest(BaseModel):
    domains: list[str] = Field(default_factory=list, max_length=30)
    user_id: str | None = Field(default=None, max_length=100)


class ImportOptions(BaseModel):
    domains: list[str] = Field(default_factory=list, max_length=30)
    resolutions: dict[str, str] = Field(default_factory=dict, max_length=1000)
    confirm: bool = False
    user_id: str | None = Field(default=None, max_length=100)

    @field_validator("resolutions")
    @classmethod
    def safe_resolution_keys(cls, value: dict[str, str]) -> dict[str, str]:
        if any(len(str(k)) > 100 or len(str(v)) > 30 for k, v in value.items()):
            raise ValueError("Conflict resolution keys and values are too long.")
        return value


class RestoreDryRunRequest(BaseModel):
    domains: list[str] = Field(default_factory=list, max_length=30)
    resolutions: dict[str, str] = Field(default_factory=dict, max_length=1000)
    user_id: str | None = Field(default=None, max_length=100)


class RestoreApplyRequest(BaseModel):
    confirm: bool = False
    domains: list[str] = Field(default_factory=list, max_length=30)
    resolutions: dict[str, str] = Field(default_factory=dict, max_length=1000)
    user_id: str | None = Field(default=None, max_length=100)


class PortabilityQuery(BaseModel):
    user_id: str | None = Field(default=None, max_length=100)
    limit: int = Field(default=50, ge=1, le=200)
