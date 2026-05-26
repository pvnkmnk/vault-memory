# daemon/auth.py
"""Authentication dependency for vault-memory daemon."""

import os
import secrets

from fastapi import Header, HTTPException
from starlette.status import HTTP_401_UNAUTHORIZED

API_KEY_HEADER = "x-api-key"


async def verify_api_key(x_api_key: str = Header(None, alias=API_KEY_HEADER)):
    """Dependency that verifies the API key from request headers.

    Uses constant-time comparison to prevent timing attacks.
    """
    expected_key = os.environ.get("VAULT_MEMORY_API_KEY")
    if not expected_key:
        # No key configured - allow requests (dev mode)
        return x_api_key

    if not x_api_key:
        raise HTTPException(
            status_code=HTTP_401_UNAUTHORIZED,
            detail="Missing API key. Provide 'x-api-key' header.",
        )

    # Use constant-time comparison to prevent timing attacks
    if not secrets.compare_digest(x_api_key, expected_key):
        raise HTTPException(
            status_code=HTTP_401_UNAUTHORIZED,
            detail="Invalid API key.",
        )
    return x_api_key
