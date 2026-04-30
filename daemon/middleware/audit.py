# daemon/middleware/audit.py
"""Audit logging middleware for API request tracking."""

import logging
import time
import uuid

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware

from daemon.middleware.correlation import correlation_id_var

# Setup audit logger
audit_logger = logging.getLogger("vault-memoryd.audit")
audit_logger.setLevel(logging.INFO)
if not audit_logger.handlers:
    audit_handler = logging.StreamHandler()
    audit_handler.setFormatter(
        logging.Formatter(
            "%(asctime)s - AUDIT - %(message)s - %(correlation_id)s - %(method)s %(path)s"
        )
    )
    audit_logger.addHandler(audit_handler)


class AuditLogMiddleware(BaseHTTPMiddleware):
    """Log all API requests with correlation IDs for audit trail."""

    async def dispatch(self, request: Request, call_next):
        AUDIT_SKIP_PATHS = {"/health", "/ready", "/metrics"}
        if request.url.path in AUDIT_SKIP_PATHS:
            return await call_next(request)

        start_time = time.time()
        correlation_id = correlation_id_var.get() or str(uuid.uuid4())

        audit_logger.info(
            "API_REQUEST_START",
            extra={
                "correlation_id": correlation_id,
                "method": request.method,
                "path": request.url.path,
                "query_params": str(request.query_params),
                "client_ip": request.client.host if request.client else "unknown",
                "user_agent": request.headers.get("user-agent", "unknown"),
            },
        )

        try:
            response = await call_next(request)
            duration_ms = (time.time() - start_time) * 1000

            audit_logger.info(
                "API_REQUEST_COMPLETE",
                extra={
                    "correlation_id": correlation_id,
                    "method": request.method,
                    "path": request.url.path,
                    "status_code": response.status_code,
                    "duration_ms": round(duration_ms, 2),
                },
            )

            endpoint = f"{request.method}:{request.url.path}"
            from daemon.health import increment_request_count
            increment_request_count(endpoint, response.status_code, duration_ms)

            return response

        except Exception as e:
            duration_ms = (time.time() - start_time) * 1000
            audit_logger.error(
                "API_REQUEST_ERROR",
                extra={
                    "correlation_id": correlation_id,
                    "method": request.method,
                    "path": request.url.path,
                    "error": str(e),
                    "duration_ms": round(duration_ms, 2),
                },
            )
            endpoint = f"{request.method}:{request.url.path}"
            from daemon.health import increment_request_count
            increment_request_count(endpoint, 500, duration_ms)
            raise
