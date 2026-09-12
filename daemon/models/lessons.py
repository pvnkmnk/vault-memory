# daemon/models/lessons.py
"""S31-4 lesson review request models."""

from typing import Optional

from pydantic import BaseModel, field_validator


class LessonPromoteRequest(BaseModel):
    """Accept a mined draft into ``lessons/``."""

    name: str
    reviewer: Optional[str] = None

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        v = (v or "").strip()
        if not v:
            raise ValueError("name cannot be empty")
        if len(v) > 300:
            raise ValueError("name too long (max 300 characters)")
        return v


class LessonRejectRequest(BaseModel):
    """Reject a mined draft and record why.

    The reason is required: it is injected into the next mining prompt for the
    project, which is the whole point of the feedback loop.
    """

    name: str
    reason: str
    reviewer: Optional[str] = None

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        v = (v or "").strip()
        if not v:
            raise ValueError("name cannot be empty")
        if len(v) > 300:
            raise ValueError("name too long (max 300 characters)")
        return v

    @field_validator("reason")
    @classmethod
    def validate_reason(cls, v: str) -> str:
        v = " ".join((v or "").split())
        if not v:
            raise ValueError("reason cannot be empty")
        if len(v) > 500:
            raise ValueError("reason too long (max 500 characters)")
        return v
