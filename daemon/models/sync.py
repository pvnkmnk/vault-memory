# daemon/models/sync.py
"""Sync operation request models."""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, field_validator


class SyncFileRequest(BaseModel):
    file_path: str


class SyncBatchRequest(BaseModel):
    paths: list[str]
    vault_path: Optional[str] = None

    @field_validator("paths")
    @classmethod
    def validate_paths(cls, v: list[str]) -> list[str]:
        if not v:
            raise ValueError("paths must not be empty")
        if len(v) > 1000:
            raise ValueError("paths cannot exceed 1000 entries")
        return v


class SyncDeltaRequest(BaseModel):
    since: str
    vault_path: str
    force_full: bool = False
    limit: int = 50
    cursor: Optional[str] = None

    @field_validator("since")
    @classmethod
    def validate_since(cls, v: str) -> str:
        try:
            datetime.fromisoformat(v)
        except ValueError:
            raise ValueError("since must be a valid ISO8601 timestamp")
        return v

    @field_validator("limit")
    @classmethod
    def validate_limit(cls, v: int) -> int:
        if v < 1:
            raise ValueError("limit must be at least 1")
        if v > 500:
            raise ValueError("limit cannot exceed 500")
        return v
