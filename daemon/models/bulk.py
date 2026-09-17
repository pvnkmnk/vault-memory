# daemon/models/bulk.py
"""Bulk operation request models."""

import ipaddress
from typing import List, Optional
from urllib.parse import urlparse

from pydantic import BaseModel, field_validator


class BulkImportRequest(BaseModel):
    notes: List[dict]
    project: Optional[str] = None
    skip_duplicates: bool = True

    @field_validator("notes")
    @classmethod
    def validate_notes(cls, v: List[dict]) -> List[dict]:
        if not v:
            raise ValueError("notes list cannot be empty")
        if len(v) > 1000:
            raise ValueError("Too many notes (max 1000 per batch)")
        for i, note in enumerate(v):
            if not isinstance(note, dict):
                raise ValueError(f"Note at index {i} must be an object")
            if "content" not in note:
                raise ValueError(f"Note at index {i} missing required field: content")
            if len(note.get("content", "")) > 100000:
                raise ValueError(
                    f"Note at index {i} content too long (max 100000 chars)"
                )
        return v


class BulkExportRequest(BaseModel):
    project: Optional[str] = None
    tags: Optional[List[str]] = None
    entity: Optional[str] = None
    date_from: Optional[str] = None
    date_to: Optional[str] = None
    limit: int = 100
    stream: bool = False

    @field_validator("limit")
    @classmethod
    def validate_limit(cls, v: int) -> int:
        if v < 1:
            raise ValueError("limit must be at least 1")
        if v > 10000:
            raise ValueError("limit cannot exceed 10000")
        return v

    @field_validator("tags")
    @classmethod
    def validate_tags(cls, v: Optional[List[str]]) -> Optional[List[str]]:
        if v is not None and len(v) > 50:
            raise ValueError("Too many tags (max 50)")
        return v


class BulkDeleteRequest(BaseModel):
    paths: List[str]
    confirm: bool = False

    @field_validator("paths")
    @classmethod
    def validate_paths(cls, v: List[str]) -> List[str]:
        if not v:
            raise ValueError("paths list cannot be empty")
        if len(v) > 1000:
            raise ValueError("Too many paths (max 1000 per batch)")
        return v

    @field_validator("confirm")
    @classmethod
    def validate_confirm(cls, v: bool) -> bool:
        if not v:
            raise ValueError("confirm must be True to perform bulk delete")
        return v


class BulkQueueRequest(BaseModel):
    notes: List[dict]
    project: str
    skip_duplicates: bool = True
    callback_url: Optional[str] = None

    @field_validator("notes")
    @classmethod
    def validate_notes(cls, v: List[dict]) -> List[dict]:
        if not v:
            raise ValueError("notes list cannot be empty")
        if len(v) > 10000:
            raise ValueError("Too many notes (max 10000 per queued batch)")
        return v

    @field_validator("callback_url")
    @classmethod
    def validate_callback_url(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        parsed = urlparse(v)
        if parsed.scheme not in ("http", "https"):
            raise ValueError("callback_url scheme must be http or https")
        hostname = parsed.hostname
        if not hostname:
            raise ValueError("callback_url must contain a valid hostname")
        if hostname.lower() in ("localhost", "127.0.0.1", "::1", "0.0.0.0"):
            raise ValueError("callback_url host cannot be loopback or internal")
        try:
            ip = ipaddress.ip_address(hostname)
            is_ip = True
        except ValueError:
            is_ip = False

        if is_ip:
            if ip.is_loopback or ip.is_private or ip.is_link_local or ip.is_multicast or ip.is_unspecified:
                raise ValueError("callback_url host cannot be private, loopback, or internal IP")
        else:
            # Not an IP address string (domain name), check for internal domains
            if hostname.lower().endswith(".local") or hostname.lower().endswith(".internal"):
                raise ValueError("callback_url host cannot be an internal domain")
        return v
