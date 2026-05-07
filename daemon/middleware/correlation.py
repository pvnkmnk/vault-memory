# daemon/middleware/correlation.py
"""Correlation ID middleware for request tracing."""

import uuid
from contextvars import ContextVar

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware

# Correlation ID context variable for request tracing
correlation_id_var: ContextVar[str] = ContextVar("correlation_id", default="")


class CorrelationMiddleware(BaseHTTPMiddleware):
    """Middleware to extract or generate correlation ID for each request."""

    async def dispatch(self, request: Request, call_next):
        correlation_id = (
            request.headers.get("X-Correlation-ID")
            or request.headers.get("x-correlation-id")
            or str(uuid.uuid4())
        )

        correlation_id_var.set(correlation_id)

        response = await call_next(request)
        response.headers["X-Correlation-ID"] = correlation_id

        return response
