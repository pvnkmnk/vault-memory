# daemon/helpers/validation.py
"""Validation and path safety helpers."""

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi.responses import JSONResponse

from daemon.dependencies import Dependencies
from daemon.helpers.responses import bad_request


def _safe_vault_path(vault_root: Path, rel_path: str) -> Path:
    """Resolve a relative path within the vault root, preventing traversal."""
    if rel_path is None:
        raise ValueError("Path cannot be null")
    candidate_rel = Path(rel_path)
    if candidate_rel.is_absolute():
        raise ValueError("Absolute paths are not allowed")
    if any(part == ".." for part in candidate_rel.parts):
        raise ValueError("Parent traversal is not allowed")
    root = vault_root.resolve()
    abs_path = (root / candidate_rel).resolve()
    abs_path.relative_to(root)
    return abs_path


def _slugify_filename(value: str) -> str:
    """Convert a title to a safe filename."""
    clean = re.sub(r"[^\w\- ]+", "", value).strip().replace(" ", "-")
    clean = re.sub(r"-{2,}", "-", clean).strip("-")
    return clean or "note"


def _slugify_title(value: str) -> str:
    """Convert a title to a URL-safe slug."""
    clean = re.sub(r"[^\w\- ]+", "", value).strip().replace(" ", "-")
    clean = re.sub(r"-{2,}", "-", clean).strip("-")
    return clean or "untitled"


def _parse_iso_date(value: Optional[str]) -> Optional[datetime]:
    """Parse an ISO8601 date string to UTC datetime."""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
        return (
            dt.astimezone(timezone.utc)
            if dt.tzinfo
            else dt.replace(tzinfo=timezone.utc)
        )
    except ValueError:
        return None


def _validate_vault_root(candidate: Path, deps: Dependencies) -> Optional[JSONResponse]:
    """Validate that a path is within the configured vault root."""
    configured_root = Path(deps.settings.vault_path).expanduser().resolve()
    try:
        candidate.relative_to(configured_root)
    except ValueError:
        return bad_request(
            "vault_path is outside the configured vault", code="UNAUTHORIZED_PATH"
        )
    if not candidate.exists():
        return bad_request("vault_path does not exist", code="INVALID_VAULT_PATH")
    return None
