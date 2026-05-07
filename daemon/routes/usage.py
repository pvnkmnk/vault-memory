# daemon/routes/usage.py
"""Usage stats route handler."""

import logging

from fastapi import APIRouter, Depends, Request

from daemon.auth import verify_api_key
from daemon.middleware.rate_limiter import rate_limiter

logger = logging.getLogger("vault-memoryd")

usage_router = APIRouter()


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

    usage = rate_limiter.get_usage(client_key)
    return usage
