# daemon/helpers/responses.py
"""Standardized error response helpers."""

from typing import Optional

from fastapi.responses import JSONResponse
from starlette.status import HTTP_500_INTERNAL_SERVER_ERROR

from daemon.models.error import ErrorResponse


def error_response(
    message: str,
    status_code: int = 500,
    detail: Optional[str] = None,
    code: Optional[str] = None,
):
    """Create a standardized error response.

    Args:
        message: User-facing error message
        status_code: HTTP status code
        detail: Technical details (not exposed to users in production)
        code: Machine-readable error code
    """
    safe_detail = detail if status_code < 500 else None
    return JSONResponse(
        status_code=status_code,
        content=ErrorResponse(
            error=message,
            detail=safe_detail,
            code=code,
        ).model_dump(exclude_none=True),
    )


def server_error(
    message: str = "Internal server error",
    code: str = "INTERNAL_ERROR",
    detail: Optional[str] = None,
):
    """Create a 500 error response."""
    return error_response(message, HTTP_500_INTERNAL_SERVER_ERROR, detail, code)


def not_found(resource: str, identifier: str):
    """Create a 404 error response."""
    return error_response(
        f"{resource} not found", 404, code=f"{resource.upper()}_NOT_FOUND"
    )


def bad_request(message: str, code: str = "BAD_REQUEST", detail: Optional[str] = None):
    """Create a 400 error response."""
    return error_response(message, 400, detail, code)
