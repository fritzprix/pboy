"""Hook metadata domain model."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator

from pboy.core.filters import is_valid_filter_expr
from pboy.models.cloudevent import current_iso_time

HookStatus = Literal["active", "paused"]


class HookCreate(BaseModel):
    """Payload to register or create a new Hook."""

    name: str = Field(min_length=1)
    topic: str = Field(min_length=1)
    filter: str | None = None
    exec: str = Field(min_length=1)
    concurrency: int = Field(default=1, ge=1)
    timeout: int = Field(default=180, ge=1)

    @field_validator("filter")
    @classmethod
    def validate_filter(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return value
        if not is_valid_filter_expr(value):
            raise ValueError(
                'Unsupported filter syntax; use path ==|!= value '
                '(e.g. .data.priority == "High")'
            )
        return value


class HookUpdate(BaseModel):
    """Payload to update an existing Hook."""

    topic: str | None = Field(default=None, min_length=1)
    filter: str | None = None
    exec: str | None = Field(default=None, min_length=1)
    concurrency: int | None = Field(default=None, ge=1)
    timeout: int | None = Field(default=None, ge=1)
    status: HookStatus | None = None

    @field_validator("filter")
    @classmethod
    def validate_filter(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return value
        if not is_valid_filter_expr(value):
            raise ValueError(
                'Unsupported filter syntax; use path ==|!= value '
                '(e.g. .data.priority == "High")'
            )
        return value


class Hook(BaseModel):
    """Registered Hook metadata."""

    name: str
    topic: str
    filter: str | None = None
    exec: str
    concurrency: int = Field(default=1, ge=1)
    timeout: int = Field(default=180, ge=1)
    created_at: str = Field(default_factory=current_iso_time)
    status: HookStatus = "active"


__all__ = [
    "Hook",
    "HookCreate",
    "HookStatus",
    "HookUpdate",
    "current_iso_time",
]
