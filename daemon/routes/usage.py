# daemon/routes/usage.py
"""Usage stats route handler."""

import logging

from typing import Optional

from fastapi import APIRouter, Depends, Request

from daemon.auth import verify_api_key
from daemon.middleware.rate_limiter import RateLimitMiddleware

logger = logging.getLogger("vault-memoryd")

usage_router = APIRouter()


def _get_rate_limiter(request: Optional[Request]) -> RateLimitMiddleware:
    """Return the live rate limiter attached to the application."""
    if request is not None:
        limiter = getattr(request.app.state, "rate_limiter", None)
        if limiter is not None:
            return limiter

    # Fallback to module-level default (e.g., during tests or misconfiguration)
    from daemon.middleware.rate_limiter import rate_limiter as default_limiter

    return default_limiter


@usage_router.get("/me/usage")
async def get_usage(
    _auth: str = Depends(verify_api_key),
    request: Request = None,
):
    """Get current API usage stats for the authenticated client."""
    api_key = request.headers.get("x-api-key") if request else None
    if api_key:
        client_key = f"key:{api_key[:8]}"
    else:
        client_key = f"ip:{request.client.host if request and request.client else 'unknown'}"

    limiter = _get_rate_limiter(request)
    usage = await limiter.get_usage(client_key)
    return usage
