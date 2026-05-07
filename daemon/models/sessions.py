# daemon/models/sessions.py
"""Session request models."""

import os
import re
from typing import List, Optional

from pydantic import BaseModel, field_validator


class SessionRegisterRequest(BaseModel):
    agent_name: str
    project: str
    task: str
    vault_path: str
    plan_ref: Optional[str] = None
    vault_paths: Optional[List[str]] = None

    @field_validator("agent_name")
    @classmethod
    def validate_agent_name(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("agent_name cannot be empty")
        if len(v) > 100:
            raise ValueError("agent_name too long (max 100 characters)")
        if not re.match(r"^[\w\-]+$", v):
            raise ValueError(
                "agent_name can only contain letters, numbers, hyphens, and underscores"
            )
        return v.strip()

    @field_validator("project")
    @classmethod
    def validate_project(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("project cannot be empty")
        if len(v) > 100:
            raise ValueError("project too long (max 100 characters)")
        if not re.match(r"^[\w\-]+$", v):
            raise ValueError(
                "project can only contain letters, numbers, hyphens, and underscores"
            )
        return v.strip()

    @field_validator("task")
    @classmethod
    def validate_task(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("task cannot be empty")
        if len(v) > 500:
            raise ValueError("task too long (max 500 characters)")
        return v.strip()

    @field_validator("vault_path")
    @classmethod
    def validate_vault_path(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("vault_path cannot be empty")
        if ".." in v:
            raise ValueError(
                "vault_path cannot contain parent directory references (..)"
            )
        if v.startswith("/") or (os.name == "nt" and len(v) > 1 and v[1] == ":"):
            pass
        return v.strip()

    @field_validator("vault_paths")
    @classmethod
    def validate_vault_paths(cls, v: Optional[List[str]]) -> Optional[List[str]]:
        if v is not None:
            if len(v) > 100:
                raise ValueError("Too many vault_paths (max 100)")
            for path in v:
                if ".." in path:
                    raise ValueError(
                        "vault_paths cannot contain parent directory references (..)"
                    )
        return v


class SessionPatchRequest(BaseModel):
    status: Optional[str] = None
    closed_at: Optional[str] = None
    notes: Optional[str] = None

    @field_validator("status")
    @classmethod
    def validate_status(cls, v: Optional[str]) -> Optional[str]:
        if v is not None:
            allowed = {"active", "closed", "paused", "error"}
            if v not in allowed:
                raise ValueError(f"status must be one of: {allowed}")
        return v

    @field_validator("notes")
    @classmethod
    def validate_notes(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and len(v) > 10000:
            raise ValueError("notes too long (max 10000 characters)")
        return v
