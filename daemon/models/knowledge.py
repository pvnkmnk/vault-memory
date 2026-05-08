# daemon/models/knowledge.py
"""Knowledge-related request models (cognify, promote, lint)."""

import re
from typing import List, Literal, Optional

from pydantic import BaseModel, field_validator


class CognifyRequest(BaseModel):
    text: str
    entity_types: Optional[List[str]] = None
    persist: bool = True

    @field_validator("text")
    @classmethod
    def validate_text(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("text cannot be empty")
        if len(v) > 50000:
            raise ValueError("text too long (max 50000 characters)")
        return v.strip()

    @field_validator("entity_types")
    @classmethod
    def validate_entity_types(cls, v: Optional[List[str]]) -> Optional[List[str]]:
        if v is not None:
            if len(v) > 20:
                raise ValueError("Too many entity_types (max 20)")
            for et in v:
                if len(et) > 50:
                    raise ValueError(
                        f"entity_type too long: {et[:20]}... (max 50 characters)"
                    )
                if not re.match(r"^[\w\-]+$", et):
                    raise ValueError(
                        f"entity_type can only contain letters, numbers, hyphens, underscores: {et}"
                    )
        return v


class PromoteRequest(BaseModel):
    text: str
    title: str
    page_type: Literal["entity", "concept", "comparison", "analysis"]
    references: List[str] = []
    vault_path: str

    @field_validator("text")
    @classmethod
    def validate_text(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("text cannot be empty")
        if len(v) > 100000:
            raise ValueError("text too long (max 100000 characters)")
        return v

    @field_validator("title")
    @classmethod
    def validate_title(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("title cannot be empty")
        if len(v) > 200:
            raise ValueError("title too long (max 200 characters)")
        return v

    @field_validator("vault_path")
    @classmethod
    def validate_vault_path(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("vault_path cannot be empty")
        if ".." in v:
            raise ValueError(
                "vault_path cannot contain parent directory references (..)"
            )
        return v

    @field_validator("references")
    @classmethod
    def validate_references(cls, v: List[str]) -> List[str]:
        if len(v) > 50:
            raise ValueError("Too many references (max 50)")
        return v


class LintRequest(BaseModel):
    vault_path: str
    stale_days: int = 30
    file_report: bool = True
