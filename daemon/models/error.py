# daemon/models/error.py
"""Standardized error response model."""

from typing import Optional

from pydantic import BaseModel


class ErrorResponse(BaseModel):
    """Standard error response format."""

    error: str
    detail: Optional[str] = None
    code: Optional[str] = None
