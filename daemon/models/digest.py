# daemon/models/digest.py
"""S32-2/3/4/5 digest and skills-export request models."""

from typing import Optional

from pydantic import BaseModel, field_validator


class DigestRequest(BaseModel):
    """Trigger one digest."""

    summarise: bool = True


class SkillsExportRequest(BaseModel):
    """Publish corroborated lessons as SKILL.md bundles."""

    project: Optional[str] = None
    #: 1 = every promoted lesson; 2 = only corroborated ones (the default for
    #: consolidation). Exports default to 1 so a single strong lesson still ships.
    min_corroboration: int = 1

    @field_validator("project")
    @classmethod
    def validate_project(cls, v: Optional[str]) -> Optional[str]:
        v = (v or "").strip()
        if v and len(v) > 200:
            raise ValueError("project too long (max 200 characters)")
        return v or None

    @field_validator("min_corroboration")
    @classmethod
    def validate_min_corroboration(cls, v: int) -> int:
        if v < 1 or v > 20:
            raise ValueError("min_corroboration must be between 1 and 20")
        return v
