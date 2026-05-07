# daemon/models/search.py
"""Search request model."""

import re
from typing import Optional

from pydantic import BaseModel, field_validator


class SearchRequest(BaseModel):
    query: str
    project: Optional[str] = None
    top_k: int = 5
    include_graph: bool = False
    include_temporal: bool = False
    time_range: Optional[dict] = None
    token_budget: Optional[int] = None

    @field_validator("query")
    @classmethod
    def validate_query(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("Query cannot be empty")
        if len(v) > 1000:
            raise ValueError("Query too long (max 1000 characters)")
        dangerous = ["<script", "javascript:", "onerror=", "onload="]
        v_lower = v.lower()
        for pattern in dangerous:
            if pattern in v_lower:
                raise ValueError(
                    f"Query contains potentially dangerous pattern: {pattern}"
                )
        return v.strip()

    @field_validator("top_k")
    @classmethod
    def validate_top_k(cls, v: int) -> int:
        if v < 1:
            raise ValueError("top_k must be at least 1")
        if v > 100:
            raise ValueError("top_k cannot exceed 100")
        return v

    @field_validator("token_budget")
    @classmethod
    def validate_token_budget(cls, v: Optional[int]) -> Optional[int]:
        if v is not None and v < 100:
            raise ValueError("token_budget must be at least 100 tokens")
        if v is not None and v > 100000:
            raise ValueError("token_budget cannot exceed 100000 tokens")
        return v

    @field_validator("project")
    @classmethod
    def validate_project(cls, v: Optional[str]) -> Optional[str]:
        if v is not None:
            if len(v) > 100:
                raise ValueError("Project name too long (max 100 characters)")
            if not re.match(r"^[\w\-]+$", v):
                raise ValueError(
                    "Project name can only contain letters, numbers, hyphens, and underscores"
                )
        return v
