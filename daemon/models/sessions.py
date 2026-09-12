# daemon/models/sessions.py
"""Session request models."""

import os
import re
from typing import List, Optional

from pydantic import BaseModel, Field, field_validator

# S31-2: the five buckets a session close can report into. Shared by the API
# model and the miner so the two cannot drift.
RECORD_FIELDS = ("decisions", "mistakes", "discoveries", "gotchas", "workflows")


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


class SessionRecordItem(BaseModel):
    """One captured fact — a decision, mistake, discovery, gotcha, or workflow."""

    content: str
    entities: Optional[List[str]] = None

    @field_validator("content")
    @classmethod
    def validate_content(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("content cannot be empty")
        if len(v) > 2000:
            raise ValueError("content too long (max 2000 characters)")
        return v.strip()

    @field_validator("entities")
    @classmethod
    def validate_entities(cls, v: Optional[List[str]]) -> Optional[List[str]]:
        if v is not None:
            if len(v) > 50:
                raise ValueError("too many entities (max 50)")
            for name in v:
                if not name or not name.strip():
                    raise ValueError("entity names cannot be empty")
        return v


class SessionRecord(BaseModel):
    """Structured capture of what actually happened in a session (S31-2)."""

    decisions: List[SessionRecordItem] = Field(default_factory=list)
    mistakes: List[SessionRecordItem] = Field(default_factory=list)
    discoveries: List[SessionRecordItem] = Field(default_factory=list)
    gotchas: List[SessionRecordItem] = Field(default_factory=list)
    workflows: List[SessionRecordItem] = Field(default_factory=list)

    def total_items(self) -> int:
        return sum(len(getattr(self, name)) for name in RECORD_FIELDS)


class SessionPatchRequest(BaseModel):
    status: Optional[str] = None
    closed_at: Optional[str] = None
    notes: Optional[str] = None
    session_record: Optional[SessionRecord] = None

    @field_validator("status")
    @classmethod
    def validate_status(cls, v: Optional[str]) -> Optional[str]:
        if v is not None:
            # Must match the agent_sessions CHECK constraint — the old
            # {active, closed, paused, error} set let two values through that
            # the database then rejected with a constraint violation.
            allowed = {"active", "idle", "closed"}
            if v not in allowed:
                raise ValueError(f"status must be one of: {sorted(allowed)}")
        return v

    @field_validator("notes")
    @classmethod
    def validate_notes(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and len(v) > 10000:
            raise ValueError("notes too long (max 10000 characters)")
        return v


class SessionLogRequest(BaseModel):
    """S31-1: one attributed file touch for a session (see daemon/helpers/attribution.py)."""

    file_path: str
    action: str = "modified"

    @field_validator("file_path")
    @classmethod
    def validate_file_path(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("file_path cannot be empty")
        if ".." in v:
            raise ValueError("file_path cannot contain parent directory references (..)")
        return v.strip()

    @field_validator("action")
    @classmethod
    def validate_action(cls, v: str) -> str:
        allowed = {"created", "modified", "deleted", "promoted"}
        if v not in allowed:
            raise ValueError(f"action must be one of: {sorted(allowed)}")
        return v


class SessionCleanupRequest(BaseModel):
    max_age_hours: int = 24

    @field_validator("max_age_hours")
    @classmethod
    def validate_max_age_hours(cls, v: int) -> int:
        if v < 1:
            raise ValueError("max_age_hours must be at least 1")
        if v > 168:
            raise ValueError("max_age_hours cannot exceed 168 (one week)")
        return v
