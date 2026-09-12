# daemon/models/ingest.py
"""S32-1 human ingestion request models."""

from typing import Optional

from pydantic import BaseModel, field_validator, model_validator


class IngestRequest(BaseModel):
    """Exactly one of ``path`` / ``url`` / ``text``."""

    path: Optional[str] = None
    url: Optional[str] = None
    text: Optional[str] = None
    force: bool = False

    @field_validator("path", "url")
    @classmethod
    def validate_short(cls, v: Optional[str]) -> Optional[str]:
        v = (v or "").strip()
        if v and len(v) > 2000:
            raise ValueError("value too long (max 2000 characters)")
        return v or None

    @field_validator("text")
    @classmethod
    def validate_text(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and not v.strip():
            raise ValueError("text cannot be empty")
        if v is not None and len(v) > 400000:
            raise ValueError("text too long (max 400000 characters)")
        return v

    @model_validator(mode="after")
    def validate_single_source(self):
        provided = [name for name in ("path", "url", "text") if getattr(self, name)]
        if len(provided) != 1:
            raise ValueError("provide exactly one of: path, url, text")
        if self.path and (".." in self.path.replace("\\", "/").split("/")):
            raise ValueError("path cannot traverse outside the vault")
        return self


class InboxRequest(BaseModel):
    """Drain the ``inbox/`` directory."""

    limit: int = 10
    force: bool = False
    remove: bool = False

    @field_validator("limit")
    @classmethod
    def validate_limit(cls, v: int) -> int:
        if v < 1 or v > 100:
            raise ValueError("limit must be between 1 and 100")
        return v
