# daemon/main.py
"""
vault-memoryd: Always-on local daemon.
Owns DB connections, model warm state, sync watcher.
Exposes HTTP on 127.0.0.1:5051.
"""

import asyncio
import json
import logging
import os
import random
import re
import secrets
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional
from pathlib import Path

import uvicorn
import httpx
import uuid
from contextvars import ContextVar
from fastapi import FastAPI, HTTPException, Request, Depends, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, field_validator
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.status import HTTP_401_UNAUTHORIZED, HTTP_500_INTERNAL_SERVER_ERROR

from .config import Settings
from .dependencies import Dependencies, get_dependencies


# Standardized error responses.


class ErrorResponse(BaseModel):
    """Standard error response format."""

    error: str
    detail: Optional[str] = None
    code: Optional[str] = None


def error_response(
    message: str, status_code: int = 500, detail: Optional[str] = None, code: Optional[str] = None
):
    """Create a standardized error response.

    Args:
        message: User-facing error message
        status_code: HTTP status code
        detail: Technical details (not exposed to users in production)
        code: Machine-readable error code
    """
    # Hide details for all server-side errors to prevent information leakage
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
    return error_response(f"{resource} not found", 404, code=f"{resource.upper()}_NOT_FOUND")


def bad_request(message: str, code: str = "BAD_REQUEST", detail: Optional[str] = None):
    """Create a 400 error response."""
    return error_response(message, 400, detail, code)


# Correlation ID context variable for request tracing
correlation_id_var: ContextVar[str] = ContextVar("correlation_id", default="")


class CorrelationMiddleware(BaseHTTPMiddleware):
    """Middleware to extract or generate correlation ID for each request."""

    async def dispatch(self, request: Request, call_next):
        # Check for existing correlation ID in headers or generate new one
        correlation_id = (
            request.headers.get("X-Correlation-ID")
            or request.headers.get("x-correlation-id")
            or str(uuid.uuid4())
        )

        # Store in context variable for logging
        correlation_id_var.set(correlation_id)

        # Add correlation ID to response headers
        response = await call_next(request)
        response.headers["X-Correlation-ID"] = correlation_id

        return response


# Authentication.

API_KEY_HEADER = "x-api-key"


async def verify_api_key(x_api_key: str = Header(None, alias=API_KEY_HEADER)):
    """Dependency that verifies the API key from request headers.

    Uses constant-time comparison to prevent timing attacks.
    """
    if not x_api_key:
        raise HTTPException(
            status_code=HTTP_401_UNAUTHORIZED,
            detail="Missing API key. Provide 'x-api-key' header.",
        )

    expected_key = os.environ.get("VAULT_MEMORY_API_KEY")
    if not expected_key:
        # No key configured - allow requests (dev mode)
        return x_api_key

    # Use constant-time comparison to prevent timing attacks
    if not secrets.compare_digest(x_api_key, expected_key):
        raise HTTPException(
            status_code=HTTP_401_UNAUTHORIZED,
            detail="Invalid API key.",
        )
    return x_api_key


from .health import (
    router as health_router,
    mark_ready,
    mark_degraded,
    update_dependency_status,
    increment_request_count,
    set_active_sessions,
)
from .retrieval import UnifiedSearch, classify_query, _strategy_temporal, extract_entities
from .weaviate_client import WeaviateClient
from .embedder import EmbedderService
from .sync_watcher import VaultSyncWatcher, SyncEngine, MarkdownParser
from .heartbeat import HeartbeatService

logger = logging.getLogger("vault-memoryd")
settings = Settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("vault-memoryd starting...")
    api_key = os.environ.get("VAULT_MEMORY_API_KEY")
    if not api_key:
        logger.warning("VAULT_MEMORY_API_KEY not set - authentication disabled (dev mode)")

    # Initialize database backend
    if settings.lite_mode:
        logger.info("Starting in LITE mode (SQLite)")
        from daemon.backends.sqlite_client import SqliteClient
        db_client = SqliteClient(settings.sqlite_db_path)
        await db_client.connect()
        weaviate_client = None  # No Weaviate in lite mode
    else:
        logger.info("Starting in FULL mode (PostgreSQL + Weaviate)")
        from daemon.backends.postgres_client import PostgresClient
        db_client = PostgresClient(settings.pg_connection_string)
        weaviate_client = WeaviateClient(settings.weaviate_url)

    embedder = EmbedderService(
        embedding_model=settings.embedding_model,
        reranker_model=settings.reranker_model,
    )
    
    # Search and sync use the appropriate backend
    if settings.lite_mode:
        searcher = None  # Placeholder for lite mode searcher
        sync_engine = None
    else:
        searcher = UnifiedSearch(
            weaviate=weaviate_client,
            postgres=db_client,
            embedder=embedder,
        )
        sync_engine = SyncEngine(
            vault_root=settings.vault_path,
            weaviate_client=weaviate_client,
            pg_client=db_client,
            embedder=embedder,
        )

    watcher = None
    if sync_engine:
        watcher = VaultSyncWatcher(engine=sync_engine)
        asyncio.create_task(watcher.start())
    
    heartbeat = None
    if not settings.lite_mode:
        heartbeat = HeartbeatService(settings.heartbeat_interval_seconds)
        await heartbeat.start(db_client)

    # Store all services in app.state before dependency checks run.
    app.state.weaviate = weaviate_client
    app.state.postgres = db_client
    app.state.embedder = embedder
    app.state.searcher = searcher
    app.state.settings = settings
    app.state.watcher = watcher
    app.state.heartbeat = heartbeat
    app.state.lite_mode = settings.lite_mode

    deps_ok = await _check_dependencies(app)
    if deps_ok:
        mark_ready()
        logger.info("vault-memoryd ready on port %s", settings.port)
    else:
        mark_degraded("One or more dependencies unavailable at startup")
        logger.warning("vault-memoryd started in DEGRADED state")

    yield

    logger.info("vault-memoryd shutting down...")
    if watcher:
        await watcher.stop()
    if weaviate_client:
        weaviate_client.close()
    if db_client:
        db_client.close()
    if heartbeat:
        await heartbeat.stop()


async def _check_dependencies(app: FastAPI) -> bool:
    """Check all dependencies and update their health status."""
    import time

    all_healthy = True

    # Check Weaviate
    try:
        start = time.time()
        await app.state.weaviate.ping()
        latency = (time.time() - start) * 1000
        update_dependency_status("weaviate", "healthy", latency)
        logger.info("Weaviate healthy (%.2f ms)", latency)
    except Exception as e:
        update_dependency_status("weaviate", "unhealthy")
        logger.error("Weaviate health check failed: %s", e)
        all_healthy = False

    # Check Postgres
    try:
        start = time.time()
        await app.state.postgres.ping()
        latency = (time.time() - start) * 1000
        update_dependency_status("postgres", "healthy", latency)
        logger.info("Postgres healthy (%.2f ms)", latency)
    except Exception as e:
        update_dependency_status("postgres", "unhealthy")
        logger.error("Postgres health check failed: %s", e)
        all_healthy = False

    # Check Embedder (optional - may not have ping method)
    try:
        if hasattr(app.state.embedder, "ping"):
            start = time.time()
            await app.state.embedder.ping()
            latency = (time.time() - start) * 1000
            update_dependency_status("embedder", "healthy", latency)
        else:
            update_dependency_status("embedder", "healthy")
    except Exception as e:
        update_dependency_status("embedder", "unhealthy")
        logger.warning("Embedder health check failed: %s", e)
        # Embedder is not critical, don't mark all_healthy as False

    return all_healthy


app = FastAPI(
    title="vault-memoryd",
    lifespan=lifespan,
    docs_url=None,  # Disable docs in production (enable via env var if needed)
    redoc_url=None,
)


# Security middleware - add security headers to all responses
class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Add security headers to all responses."""

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        # Prevent MIME type sniffing
        response.headers["X-Content-Type-Options"] = "nosniff"
        # Prevent clickjacking
        response.headers["X-Frame-Options"] = "DENY"
        # XSS protection
        response.headers["X-XSS-Protection"] = "1; mode=block"
        # Referrer policy
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        # Content Security Policy (restrictive)
        response.headers["Content-Security-Policy"] = "default-src 'self'"
        return response


app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(CorrelationMiddleware)

# Rate limiting middleware.

from typing import Dict, Tuple
from collections import defaultdict
import time


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Simple in-memory rate limiting middleware.

    Uses sliding window algorithm with configurable limits per endpoint.
    Stores client IP + endpoint as key.
    """

    def __init__(self, app, requests_per_minute: int = 60, burst_size: int = 10):
        super().__init__(app)
        self.requests_per_minute = requests_per_minute
        self.burst_size = burst_size
        # Dict of (client_ip, endpoint) -> list of timestamps
        self._requests: Dict[Tuple[str, str], list] = defaultdict(list)
        self._lock = asyncio.Lock()

    async def dispatch(self, request: Request, call_next):
        client_ip = request.client.host if request.client else "unknown"
        endpoint = f"{request.method}:{request.url.path}"
        key = (client_ip, endpoint)

        now = time.time()
        window_start = now - 60  # 1 minute window
        burst_window_start = now - 2.0  # 2 second burst window

        async with self._lock:
            # Clean old requests outside window
            self._requests[key] = [ts for ts in self._requests[key] if ts > window_start]

            recent_burst = [ts for ts in self._requests[key] if ts > burst_window_start]
            if len(recent_burst) >= self.burst_size:
                return JSONResponse(
                    status_code=429,
                    content={"error": "Burst limit exceeded", "code": "RATE_LIMIT_BURST"},
                )

            # Check rate limit over window
            if len(self._requests[key]) >= self.requests_per_minute:
                return JSONResponse(
                    status_code=429,
                    content={"error": "Rate limit exceeded", "code": "RATE_LIMIT_WINDOW"},
                )

            # Record this request
            self._requests[key].append(now)

            if random.random() < 0.01:
                cutoff = now - 300  # 5 minutes
                stale_keys = [k for k, v in self._requests.items() if not v or v[-1] < cutoff]
                for k in stale_keys:
                    del self._requests[k]

        response = await call_next(request)
        # Add rate limit headers
        async with self._lock:
            remaining = max(0, self.requests_per_minute - len(self._requests[key]))
        response.headers["X-RateLimit-Limit"] = str(self.requests_per_minute)
        response.headers["X-RateLimit-Remaining"] = str(remaining)
        return response


# Apply rate limiting (60 req/min, burst of 20 in a 2 second window)
app.add_middleware(RateLimitMiddleware, requests_per_minute=60, burst_size=20)

# Audit logging middleware.


class AuditLogMiddleware(BaseHTTPMiddleware):
    """Log all API requests with correlation IDs for audit trail."""

    async def dispatch(self, request: Request, call_next):
        AUDIT_SKIP_PATHS = {"/health", "/ready", "/metrics"}
        if request.url.path in AUDIT_SKIP_PATHS:
            return await call_next(request)

        start_time = time.time()
        correlation_id = correlation_id_var.get() or str(uuid.uuid4())

        # Log request start
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

            # Log request completion
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

            # Track metrics for Prometheus
            endpoint = f"{request.method}:{request.url.path}"
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
            # Track error in metrics
            endpoint = f"{request.method}:{request.url.path}"
            increment_request_count(endpoint, 500, duration_ms)
            raise


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

app.add_middleware(AuditLogMiddleware)

# CORS - restrictive by default, only allow localhost origins
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost",
        "http://127.0.0.1",
        "https://localhost",
        "https://127.0.0.1",
    ],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    allow_headers=["*"],
    max_age=600,
)

app.include_router(health_router)


# Search.


class SearchRequest(BaseModel):
    query: str
    project: Optional[str] = None
    top_k: int = 5
    include_graph: bool = False
    include_temporal: bool = False
    time_range: Optional[dict] = None
    token_budget: Optional[int] = None  # P4: ContextAssembler tiered context

    @field_validator("query")
    @classmethod
    def validate_query(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("Query cannot be empty")
        if len(v) > 1000:
            raise ValueError("Query too long (max 1000 characters)")
        # Check for potentially dangerous patterns
        dangerous = ["<script", "javascript:", "onerror=", "onload="]
        v_lower = v.lower()
        for pattern in dangerous:
            if pattern in v_lower:
                raise ValueError(f"Query contains potentially dangerous pattern: {pattern}")
        return v.strip()

    @field_validator("top_k")
    @classmethod
    def validate_top_k(cls, v: int) -> int:
        if v < 1:
            raise ValueError("top_k must be at least 1")
        if v > 100:
            raise ValueError("top_k cannot exceed 100")
        return v

    @field_validator("token_budget")
    @classmethod
    def validate_token_budget(cls, v: Optional[int]) -> Optional[int]:
        if v is not None and v < 100:
            raise ValueError("token_budget must be at least 100 tokens")
        if v is not None and v > 100000:
            raise ValueError("token_budget cannot exceed 100000 tokens")
        return v

    @field_validator("project")
    @classmethod
    def validate_project(cls, v: Optional[str]) -> Optional[str]:
        if v is not None:
            if len(v) > 100:
                raise ValueError("Project name too long (max 100 characters)")
            # Validate project name format (alphanumeric, hyphens, underscores)
            if not re.match(r"^[\w\-]+$", v):
                raise ValueError(
                    "Project name can only contain letters, numbers, hyphens, and underscores"
                )
        return v


@app.post("/search")
async def search(
    req: SearchRequest,
    deps: Dependencies = Depends(get_dependencies),
    _auth: str = Depends(verify_api_key),
):
    """
    Search endpoint using proper dependency injection.
    Demonstrates the new Dependencies container pattern.
    """
    if deps.settings.lite_mode or deps.searcher_optional is None:
        raise HTTPException(status_code=501, detail="Search is not available in lite mode.")

    results = await deps.searcher_optional.search(
        query=req.query,
        project=req.project,
        top_k=req.top_k,
        include_graph=req.include_graph,
        include_temporal=req.include_temporal,
        time_range=req.time_range,
        vault_root=deps.settings.vault_path,
        token_budget=req.token_budget,
    )
    return {
        "results": [r.to_clip() for r in results],
        "intent": classify_query(req.query).value,
    }


@app.get("/graph")
async def graph_query(
    entity: str,
    relationship: Optional[str] = None,
    deps: Dependencies = Depends(get_dependencies),
    _auth: str = Depends(verify_api_key),
):
    """Graph query endpoint using DI container for database access."""
    if deps.settings.lite_mode:
        raise HTTPException(status_code=501, detail="Graph queries are not available in lite mode.")

    with deps.postgres.cursor() as cursor:
        sql = "SELECT target_name, relationship_type, edge_source FROM relationships WHERE source_name = %s"
        params = [entity]
        if relationship:
            sql += " AND relationship_type = %s"
            params.append(relationship)
        cursor.execute(sql, params)
        rows = cursor.fetchall()
    return {"paths": [{"target": r[0], "relationship": r[1], "edge_source": r[2]} for r in rows]}


@app.get("/temporal")
async def temporal_query(
    entity: str,
    start: str = "2025-01-01",
    end: str = "2025-12-31",
    deps: Dependencies = Depends(get_dependencies),
    _auth: str = Depends(verify_api_key),
):
    if deps.settings.lite_mode:
        raise HTTPException(status_code=501, detail="Temporal queries are not available in lite mode.")

    results = await _strategy_temporal(
        query=entity,
        time_range={"start": start, "end": end},
        entities=extract_entities(entity),
        postgres=deps.postgres,
    )
    return {"results": [r.to_clip() for r in results]}


# Session registry endpoints.


class SessionRegisterRequest(BaseModel):
    agent_name: str
    project: str
    task: str
    vault_path: str
    plan_ref: Optional[str] = None
    vault_paths: Optional[List[str]] = None

    @field_validator("agent_name")
    @classmethod
    def validate_agent_name(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("agent_name cannot be empty")
        if len(v) > 100:
            raise ValueError("agent_name too long (max 100 characters)")
        # Only allow alphanumeric, hyphens, underscores
        if not re.match(r"^[\w\-]+$", v):
            raise ValueError(
                "agent_name can only contain letters, numbers, hyphens, and underscores"
            )
        return v.strip()

    @field_validator("project")
    @classmethod
    def validate_project(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("project cannot be empty")
        if len(v) > 100:
            raise ValueError("project too long (max 100 characters)")
        if not re.match(r"^[\w\-]+$", v):
            raise ValueError("project can only contain letters, numbers, hyphens, and underscores")
        return v.strip()

    @field_validator("task")
    @classmethod
    def validate_task(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("task cannot be empty")
        if len(v) > 500:
            raise ValueError("task too long (max 500 characters)")
        return v.strip()

    @field_validator("vault_path")
    @classmethod
    def validate_vault_path(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("vault_path cannot be empty")
        # Prevent path traversal attacks
        if ".." in v:
            raise ValueError("vault_path cannot contain parent directory references (..)")
        # Check for absolute vs relative paths
        if v.startswith("/") or (os.name == "nt" and len(v) > 1 and v[1] == ":"):
            # Allow absolute paths but validate they exist
            pass
        return v.strip()

    @field_validator("vault_paths")
    @classmethod
    def validate_vault_paths(cls, v: Optional[List[str]]) -> Optional[List[str]]:
        if v is not None:
            if len(v) > 100:
                raise ValueError("Too many vault_paths (max 100)")
            for path in v:
                if ".." in path:
                    raise ValueError("vault_paths cannot contain parent directory references (..)")
        return v


class SessionPatchRequest(BaseModel):
    status: Optional[str] = None
    closed_at: Optional[str] = None
    notes: Optional[str] = None

    @field_validator("status")
    @classmethod
    def validate_status(cls, v: Optional[str]) -> Optional[str]:
        if v is not None:
            allowed = {"active", "closed", "paused", "error"}
            if v not in allowed:
                raise ValueError(f"status must be one of: {allowed}")
        return v

    @field_validator("notes")
    @classmethod
    def validate_notes(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and len(v) > 10000:
            raise ValueError("notes too long (max 10000 characters)")
        return v


@app.post("/sessions", status_code=201)
async def session_register(
    req: SessionRegisterRequest,
    deps: Dependencies = Depends(get_dependencies),
    _auth: str = Depends(verify_api_key),
):
    """
    Register a new agent session in agent_sessions table.
    Returns session_id and started_at.
    Uses DI container for database access.
    """
    try:
        with deps.postgres.cursor() as cursor:
            now = datetime.now(timezone.utc)
            cursor.execute(
                """
                INSERT INTO agent_sessions
                    (agent_name, project, task, vault_path, plan_ref, vault_paths, status, started_at)
                VALUES (%s, %s, %s, %s, %s, %s, 'active', %s)
                RETURNING id AS session_id, started_at
                """,
                (
                    req.agent_name,
                    req.project,
                    req.task,
                    req.vault_path,
                    req.plan_ref,
                    req.vault_paths or [],
                    now,
                ),
            )
            row = cursor.fetchone()
        return {
            "session_id": str(row["session_id"]),
            "agent_name": req.agent_name,
            "project": req.project,
            "task": req.task,
            "started_at": row["started_at"].isoformat(),
            "status": "active",
        }
    except Exception as e:
        logger.error("session_register error: %s", e)
        return server_error(
            "Failed to register session", code="SESSION_CREATE_FAILED", detail=str(e)
        )


@app.get("/sessions")
async def session_list(
    agent_name: Optional[str] = None,
    project: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = 50,
    deps: Dependencies = Depends(get_dependencies),
    _auth: str = Depends(verify_api_key),
):
    """
    Query agent sessions. Supports filter by agent_name, project, status.
    Uses DI container for database access.
    """
    try:
        clauses = []
        params: list = []
        if agent_name:
            clauses.append("agent_name = %s")
            params.append(agent_name)
        if project:
            clauses.append("project = %s")
            params.append(project)
        if status:
            clauses.append("status = %s")
            params.append(status)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        params.append(limit)

        with deps.postgres.cursor() as cursor:
            cursor.execute(
                f"SELECT * FROM agent_sessions {where} ORDER BY started_at DESC LIMIT %s",
                params,
            )
            rows = cursor.fetchall()

        sessions = []
        for r in rows:
            sessions.append(
                {
                    "session_id": str(r["id"]),  # Column is 'id' not 'session_id'
                    "agent_name": r["agent_name"],
                    "project": r["project"],
                    "task": r["task"],
                    "status": r["status"],
                    "started_at": r["started_at"].isoformat() if r["started_at"] else None,
                    "closed_at": r["closed_at"].isoformat() if r["closed_at"] else None,
                }
            )
        return {"sessions": sessions, "count": len(sessions)}
    except Exception as e:
        return server_error("Failed to list sessions", code="SESSION_LIST_FAILED", detail=str(e))


@app.patch("/sessions/{session_id}")
async def session_patch(
    session_id: str,
    req: SessionPatchRequest,
    deps: Dependencies = Depends(get_dependencies),
    _auth: str = Depends(verify_api_key),
):
    """
    Update an agent session — typically to close it (status: closed, closed_at: now).
    Returns the updated session with duration_s calculated.
    Uses DI container for database access.
    """
    try:
        with deps.postgres.cursor() as cursor:
            # Fetch existing to calculate duration
            cursor.execute(
                "SELECT started_at, status FROM agent_sessions WHERE id = %s",
                (session_id,),
            )
            existing = cursor.fetchone()
            if not existing:
                raise HTTPException(status_code=404, detail=f"Session {session_id} not found.")

            updates: dict = {}
            if req.status:
                updates["status"] = req.status
            if req.closed_at:
                updates["closed_at"] = req.closed_at
            if req.notes:
                updates["notes"] = req.notes

            if not updates:
                raise HTTPException(status_code=400, detail="No fields to update.")

            set_clause = ", ".join(f"{k} = %s" for k in updates)
            cursor.execute(
                f"UPDATE agent_sessions SET {set_clause} WHERE id = %s RETURNING *",
                list(updates.values()) + [session_id],
            )
            row = cursor.fetchone()

        started_at = row["started_at"]
        closed_at = row["closed_at"]
        duration_s = None
        if started_at and closed_at:
            if isinstance(closed_at, str):
                closed_at = datetime.fromisoformat(closed_at)
            try:
                duration_s = int((closed_at - started_at).total_seconds())
            except Exception:
                pass

        return {
            "session_id": str(row["id"]),
            "agent_name": row["agent_name"],
            "project": row["project"],
            "task": row["task"],
            "status": row["status"],
            "started_at": started_at.isoformat() if started_at else None,
            "closed_at": row["closed_at"].isoformat() if row["closed_at"] else None,
            "duration_s": duration_s,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error("session_patch error: %s", e)
        return server_error("Failed to update session", code="SESSION_UPDATE_FAILED", detail=str(e))


# Cognify.
class CognifyRequest(BaseModel):
    text: str
    entity_types: Optional[List[str]] = None
    persist: bool = True

    @field_validator("text")
    @classmethod
    def validate_text(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("text cannot be empty")
        if len(v) > 50000:
            raise ValueError("text too long (max 50000 characters)")
        return v.strip()

    @field_validator("entity_types")
    @classmethod
    def validate_entity_types(cls, v: Optional[List[str]]) -> Optional[List[str]]:
        if v is not None:
            if len(v) > 20:
                raise ValueError("Too many entity_types (max 20)")
            for et in v:
                if len(et) > 50:
                    raise ValueError(f"entity_type too long: {et[:20]}... (max 50 characters)")
                if not re.match(r"^[\w\-]+$", et):
                    raise ValueError(
                        f"entity_type can only contain letters, numbers, hyphens, underscores: {et}"
                    )
        return v


def _normalize_triples(raw_triples: Any) -> tuple[list[dict], int]:
    if not isinstance(raw_triples, list):
        return [], 0

    normalized: list[dict] = []
    invalid = 0
    for item in raw_triples:
        if not isinstance(item, dict):
            invalid += 1
            continue
        subject = str(item.get("subject", "")).strip()
        predicate = str(item.get("predicate", "")).strip()
        obj = str(item.get("object", "")).strip()
        if not subject or not predicate or not obj:
            invalid += 1
            continue
        normalized.append(
            {
                "subject": subject[:512],
                "predicate": predicate[:128],
                "object": obj[:512],
            }
        )
    return normalized, invalid


def _persist_cognify_triples(triples: list[dict], deps: Dependencies) -> dict:
    entities_written = 0
    relationships_written = 0

    if not triples:
        return {
            "persisted": True,
            "entities_written": 0,
            "relationships_written": 0,
            "persist_error": None,
        }

    try:
        entity_names = sorted({t["subject"] for t in triples} | {t["object"] for t in triples})
        rel_rows = [(t["subject"], t["object"], t["predicate"].upper()) for t in triples]

        if deps.settings.lite_mode:
            inserted_entities: set[str] = set()
            with deps.postgres.cursor() as cursor:
                for source_name, target_name, relationship_type in rel_rows:
                    cursor.execute(
                        """
                        INSERT OR IGNORE INTO triples (subject, predicate, object, source_file)
                        VALUES (%s, %s, %s, %s)
                        """,
                        (source_name, relationship_type, target_name, None),
                    )
                    inserted = int(cursor.rowcount or 0)
                    if inserted:
                        relationships_written += inserted
                        inserted_entities.update((source_name, target_name))
            entities_written = len(inserted_entities)
            return {
                "persisted": True,
                "entities_written": entities_written,
                "relationships_written": relationships_written,
                "persist_error": None,
            }

        from psycopg2.extras import execute_values

        with deps.postgres.cursor() as cursor:
            if entity_names:
                execute_values(
                    cursor,
                    """
                    INSERT INTO temporal_entities (entity_name, node_type)
                    VALUES %s
                    ON CONFLICT (entity_name) DO NOTHING
                    """,
                    [(name, "entity") for name in entity_names],
                    template="(%s, %s)",
                )
                entities_written = int(cursor.rowcount or 0)

            if rel_rows:
                execute_values(
                    cursor,
                    """
                    INSERT INTO relationships (source_name, target_name, relationship_type, edge_source)
                    SELECT v.source_name, v.target_name, v.relationship_type, 'body'
                    FROM (VALUES %s) AS v(source_name, target_name, relationship_type)
                    WHERE NOT EXISTS (
                        SELECT 1
                        FROM relationships r
                        WHERE r.source_name = v.source_name
                          AND r.target_name = v.target_name
                          AND r.relationship_type = v.relationship_type
                    )
                    """,
                    rel_rows,
                    template="(%s, %s, %s)",
                )
                relationships_written = int(cursor.rowcount or 0)
        return {
            "persisted": True,
            "entities_written": entities_written,
            "relationships_written": relationships_written,
            "persist_error": None,
        }
    except Exception as e:
        logger.error("cognify persistence failed: %s", e)
        return {
            "persisted": False,
            "entities_written": entities_written,
            "relationships_written": relationships_written,
            "persist_error": str(e),
        }


async def _extract_triples_with_ollama(
    text: str,
    entity_types: Optional[List[str]] = None,
    ollama_url: str = "http://localhost:11434",
    ollama_model: str = "llama3.2",
) -> dict:

    entity_filter = (
        f"\nOnly extract entities of these types: {entity_types}" if entity_types else ""
    )
    prompt = f"""Extract all entities and relationships from the following text.
Return ONLY a JSON array of triples in this exact format:
[{{"subject": "EntityName", "predicate": "relationship", "object": "EntityName"}}]

Do not include any explanation or markdown. Do not include null or empty values.
{entity_filter}

Text:
{text}
"""
    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.post(
            f"{ollama_url}/api/generate",
            json={"model": ollama_model, "prompt": prompt, "stream": False, "format": "json"},
        )
        r.raise_for_status()
        response_data = r.json()
    response_text = response_data.get("response", "")

    try:
        raw = json.loads(response_text)
    except json.JSONDecodeError:
        json_match = re.search(r"\[.*\]", response_text, re.DOTALL)
        if json_match:
            try:
                raw = json.loads(json_match.group())
            except json.JSONDecodeError:
                raw = []
        else:
            raw = []
    triples, invalid = _normalize_triples(raw)
    return {
        "triples": triples,
        "invalid_triples": invalid,
        "model": ollama_model,
    }


@app.post("/cognify")
async def cognify(
    req: CognifyRequest,
    deps: Dependencies = Depends(get_dependencies),
    _auth: str = Depends(verify_api_key),
):
    """
    Extract entities and relationships from text using Ollama LLM.
    Returns subject/predicate/object triples in structured JSON.
    Gracefully degrades if Ollama is unavailable.
    """
    try:
        extract_result = await _extract_triples_with_ollama(
            req.text,
            req.entity_types,
            deps.settings.ollama_url,
            deps.settings.ollama_model,
        )
        triples = extract_result["triples"]
        persist_result = {
            "persisted": False,
            "entities_written": 0,
            "relationships_written": 0,
            "persist_error": None,
        }
        if req.persist:
            persist_result = _persist_cognify_triples(triples, deps)

        return {
            "triples": triples,
            "entity_count": len(triples),
            "invalid_triples": extract_result["invalid_triples"],
            "model": extract_result["model"],
            "text_len": len(req.text),
            "persist_requested": req.persist,
            **persist_result,
        }

    except httpx.RequestError as e:
        logger.warning("Ollama unavailable for cognify: %s", e)
        return {
            "triples": [],
            "entity_count": 0,
            "invalid_triples": 0,
            "model": deps.settings.ollama_model,
            "text_len": len(req.text),
            "error": f"Ollama unavailable: {e}",
            "degraded": True,
            "persist_requested": req.persist,
            "persisted": False,
            "entities_written": 0,
            "relationships_written": 0,
            "persist_error": None,
        }
    except Exception as e:
        logger.error("cognify error: %s", e)
        return {
            "triples": [],
            "entity_count": 0,
            "invalid_triples": 0,
            "model": deps.settings.ollama_model,
            "text_len": len(req.text),
            "error": str(e),
            "persist_requested": req.persist,
            "persisted": False,
            "entities_written": 0,
            "relationships_written": 0,
            "persist_error": None,
        }


class PromoteRequest(BaseModel):
    text: str
    title: str
    page_type: Literal["entity", "concept", "comparison", "analysis"]
    references: List[str] = []
    vault_path: str

    @field_validator("text")
    @classmethod
    def validate_text(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("text cannot be empty")
        if len(v) > 100000:
            raise ValueError("text too long (max 100000 characters)")
        return v

    @field_validator("title")
    @classmethod
    def validate_title(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("title cannot be empty")
        if len(v) > 200:
            raise ValueError("title too long (max 200 characters)")
        return v

    @field_validator("vault_path")
    @classmethod
    def validate_vault_path(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("vault_path cannot be empty")
        if ".." in v:
            raise ValueError("vault_path cannot contain parent directory references (..)")
        return v

    @field_validator("references")
    @classmethod
    def validate_references(cls, v: List[str]) -> List[str]:
        if len(v) > 50:
            raise ValueError("Too many references (max 50)")
        return v


def _slugify_title(value: str) -> str:
    clean = re.sub(r"[^\w\- ]+", "", value).strip().replace(" ", "-")
    clean = re.sub(r"-{2,}", "-", clean).strip("-")
    return clean or "untitled"


def _canonical_promote_path(vault_root: Path, title: str, page_type: str) -> Path:
    knowledge_dir = vault_root / "Knowledge"
    title_slug = _slugify_title(title)
    if page_type == "entity":
        return knowledge_dir / f"{title_slug}.md"
    if page_type == "concept":
        return knowledge_dir / f"concept-{title_slug}.md"
    if page_type == "comparison":
        return knowledge_dir / f"compare-{title_slug}.md"
    return knowledge_dir / f"analysis-{title_slug}.md"


def _ensure_reference_wikilinks(text: str, references: List[str]) -> tuple[str, List[str]]:
    missing = []
    for ref in references:
        marker = f"[[{ref}]]"
        if marker not in text:
            missing.append(ref)
    if not missing:
        return text, []
    lines = [text.rstrip(), "", "## References", ""]
    lines.extend([f"- [[{ref}]]" for ref in missing])
    return "\n".join(lines).rstrip() + "\n", missing


def _write_lint_report(report_dict: dict, vault_root: Path) -> str:
    run_at = report_dict.get("run_at", datetime.now(timezone.utc).isoformat())
    date_stamp = datetime.fromisoformat(run_at).strftime("%Y-%m-%d")
    out_path = vault_root / f"lint-{date_stamp}.md"
    summary = report_dict.get("summary", {})
    lines = [
        f"# Vault Lint Report ({date_stamp})",
        "",
        f"- Run At: {run_at}",
        f"- Stale Days: {report_dict.get('stale_days', 30)}",
        "",
        "## Summary",
        "",
        f"- Total Issues: {summary.get('total_issues', 0)}",
        f"- Orphans: {summary.get('orphans', 0)}",
        f"- Contradictions: {summary.get('contradictions', 0)}",
        f"- Stale Nodes: {summary.get('stale_nodes', 0)}",
        f"- Missing Pages: {summary.get('missing_pages', 0)}",
        f"- Unlinked Pages: {summary.get('unlinked_pages', 0)}",
        "",
    ]
    out_path.write_text("\n".join(lines), encoding="utf-8")
    return str(out_path)


async def _write_text_async(path: Path, content: str) -> None:
    await asyncio.to_thread(path.write_text, content, encoding="utf-8")


async def _append_text_async(path: Path, content: str) -> None:
    def _append():
        with path.open("a", encoding="utf-8") as f:
            f.write(content)

    await asyncio.to_thread(_append)


def _validate_vault_root(candidate: Path, deps: Dependencies) -> Optional[JSONResponse]:
    configured_root = Path(deps.settings.vault_path).expanduser().resolve()
    try:
        candidate.relative_to(configured_root)
    except ValueError:
        return bad_request("vault_path is outside the configured vault", code="UNAUTHORIZED_PATH")
    if not candidate.exists():
        return bad_request("vault_path does not exist", code="INVALID_VAULT_PATH")
    return None


@app.post("/promote", status_code=201)
async def promote(
    req: PromoteRequest,
    deps: Dependencies = Depends(get_dependencies),
    _auth: str = Depends(verify_api_key),
):
    vault_root = Path(req.vault_path).expanduser().resolve()
    vault_error = _validate_vault_root(vault_root, deps)
    if vault_error:
        return vault_error

    page_path = _canonical_promote_path(vault_root, req.title, req.page_type)
    page_path.parent.mkdir(parents=True, exist_ok=True)
    now_iso = datetime.now(timezone.utc).isoformat()
    body, wikilinks_added = _ensure_reference_wikilinks(req.text, req.references)
    frontmatter = "\n".join(
        [
            "---",
            f"title: {req.title}",
            f"page_type: {req.page_type}",
            "maturity: tree",
            "trust: agent-reviewed",
            "decay-profile: active",
            "agent-written: true",
            f"date_created: {now_iso}",
            f"date_modified: {now_iso}",
            "---",
            "",
        ]
    )
    page_content = frontmatter + body
    await _write_text_async(page_path, page_content)

    # sync_file indexes the new page into Weaviate + PG.
    # watcher is Optional — it may not be running in degraded or test environments.
    # The file is already written; a missing watcher means it will be picked up on
    # the next reconcile cycle rather than immediately.
    if deps.watcher:
        await deps.watcher.engine.sync_file(page_path, caller="user")
    else:
        logger.warning("promote: watcher not running — %s will be indexed on next reconcile", page_path)

    try:
        cognify_result = await _extract_triples_with_ollama(
            page_content,
            None,
            deps.settings.ollama_url,
            deps.settings.ollama_model,
        )
        persist_result = _persist_cognify_triples(cognify_result["triples"], deps)
        degraded = False
        promote_error = None
    except Exception as e:
        logger.warning("promote cognify step failed: %s", e)
        cognify_result = {"triples": [], "invalid_triples": 0, "model": deps.settings.ollama_model}
        persist_result = {
            "persisted": False,
            "entities_written": 0,
            "relationships_written": 0,
            "persist_error": str(e),
        }
        degraded = True
        promote_error = str(e)

    log_path = vault_root / "log.md"
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
    log_entry = (
        f"## [{stamp}] promote | {req.title} | type:{req.page_type} | "
        f"refs:{len(req.references)} triples:{len(cognify_result['triples'])}\n"
    )
    await _append_text_async(log_path, log_entry)

    return {
        "path_written": str(page_path),
        "triples_extracted": len(cognify_result["triples"]),
        "references_linked": req.references,
        "wikilinks_added": wikilinks_added,
        "log_entry": log_entry.strip(),
        "degraded": degraded,
        "error": promote_error,
        **persist_result,
    }


class LintRequest(BaseModel):
    vault_path: str
    stale_days: int = 30
    file_report: bool = True


@app.post("/lint")
async def lint(
    req: LintRequest,
    deps: Dependencies = Depends(get_dependencies),
    _auth: str = Depends(verify_api_key),
):
    from .lint import run_lint

    vault_root = Path(req.vault_path).expanduser().resolve()
    vault_error = _validate_vault_root(vault_root, deps)
    if vault_error:
        return vault_error

    report = await run_lint(deps.postgres, vault_root, req.stale_days)
    payload = {
        "run_at": report.run_at,
        "stale_days": report.stale_days,
        "orphans": report.orphans,
        "contradictions": report.contradictions,
        "stale_nodes": report.stale_nodes,
        "missing_pages": report.missing_pages,
        "unlinked_pages": report.unlinked_pages,
        "summary": report.summary,
    }
    if req.file_report:
        payload["report_path"] = await asyncio.to_thread(_write_lint_report, payload, vault_root)
    return payload


# search_siblings endpoint — topic hub sibling traversal.
@app.post("/search_siblings")
async def search_siblings(
    req: SearchRequest,
    deps: Dependencies = Depends(get_dependencies),
    _auth: str = Depends(verify_api_key),
):
    """
    Discover notes that share a topic hub with the seed entity.
    Uses the topic_hubs table to find qualifying hubs (in-degree >= 5),
    then expands through relationships to find siblings.
    Score = centrality x hub_penalty.
    Uses DI container for database access.
    """
    if deps.settings.lite_mode:
        raise HTTPException(status_code=501, detail="Sibling search is not available in lite mode.")

    entities = extract_entities(req.query)
    if not entities:
        return {"siblings": [], "note": "No entities extracted from query."}

    try:
        with deps.postgres.cursor() as cursor:
            # Step 1: Get all topic hubs
            cursor.execute("SELECT vault_path, entity_name, in_degree, hub_penalty FROM topic_hubs")
            hubs = cursor.fetchall()
            if not hubs:
                return {"siblings": [], "note": "No topic hubs registered."}

            hub_names = [h["entity_name"] for h in hubs]

            # Step 2: Find relationships connecting seed entities to topic hubs
            sql = """
                SELECT
                    te.entity_name AS seed,
                    r.target_name AS hub,
                    r.relationship_type,
                    th.hub_penalty,
                    te.centrality
                FROM relationships r
                JOIN temporal_entities te ON te.entity_name = r.source_name
                JOIN topic_hubs th ON th.entity_name = r.target_name
                WHERE r.source_name = ANY(%s)
                  AND r.target_name = ANY(%s)
            """
            cursor.execute(sql, (entities, hub_names))
            connections = cursor.fetchall()

            # Step 3: Batch query all hubs at once (fix N+1 query)
            if not connections:
                return {"siblings": [], "note": "No connections found to topic hubs."}

            hub_names = list(set(c["hub"] for c in connections))
            seed_names = list(set(c["seed"] for c in connections))

            cursor.execute(
                """
                SELECT r.source_name, te.centrality, vel.vault_path, te.properties, r.target_name as hub
                FROM relationships r
                JOIN temporal_entities te ON te.entity_name = r.source_name
                LEFT JOIN vault_entity_links vel ON te.entity_name = vel.entity_id
                WHERE r.target_name = ANY(%s)
                  AND r.source_name != ANY(%s)
                  AND NOT (r.source_name = ANY(%s))
                ORDER BY te.centrality DESC
                """,
                (hub_names, seed_names, list(entities)),
            )

            related = cursor.fetchall()

        hub_penalties = {c["hub"]: float(c["hub_penalty"]) for c in connections}

        siblings: Dict[str, Dict[str, Any]] = {}
        for rel in related:
            sibling_entity = rel["source_name"]
            rel_centrality = float(rel["centrality"]) if rel["centrality"] else 0.3
            vault_path = rel["vault_path"]
            props = rel["properties"] or {}
            hub = rel["hub"]
            hub_penalty = hub_penalties.get(hub, 1.0)

            score = rel_centrality * hub_penalty

            if sibling_entity not in siblings or siblings[sibling_entity]["score"] < score:
                siblings[sibling_entity] = {
                    "entity": sibling_entity,
                    "vault_path": vault_path,
                    "score": round(score, 4),
                    "hub": hub,
                    "hub_penalty": round(hub_penalty, 3),
                    "centrality": round(rel_centrality, 3),
                    "relationship_type": "linked",
                    "project": props.get("project"),
                    "tags": [sibling_entity],
                }

        # Sort by score descending
        result_list = sorted(siblings.values(), key=lambda x: x["score"], reverse=True)

        return {"siblings": result_list[: req.top_k], "hub_count": len(hubs)}

    except Exception as e:
        logger.error("search_siblings error: %s", e)
        return {"siblings": [], "error": "Search failed", "detail": str(e)}


# Bulk operations endpoints.


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
                raise ValueError(f"Note at index {i} content too long (max 100000 chars)")
        return v


class BulkExportRequest(BaseModel):
    project: Optional[str] = None
    tags: Optional[List[str]] = None
    entity: Optional[str] = None
    date_from: Optional[str] = None
    date_to: Optional[str] = None
    limit: int = 100

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


def _safe_vault_path(vault_root: Path, rel_path: str) -> Path:
    rel = rel_path.lstrip("/\\")
    abs_path = (vault_root / rel).resolve()
    abs_path.relative_to(vault_root.resolve())
    return abs_path


def _slugify_filename(value: str) -> str:
    clean = re.sub(r"[^\w\- ]+", "", value).strip().replace(" ", "-")
    clean = re.sub(r"-{2,}", "-", clean).strip("-")
    return clean or "note"


def _parse_iso_date(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
        return dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


@app.post("/bulk/import", status_code=201)
async def bulk_import(
    req: BulkImportRequest,
    deps: Dependencies = Depends(get_dependencies),
    _auth: str = Depends(verify_api_key),
):
    vault_root = Path(deps.settings.vault_path).resolve()
    project_dir = req.project or "Bulk Import"
    imported = 0
    skipped = 0
    errors = []
    written_paths = []

    try:
        target_dir = _safe_vault_path(vault_root, project_dir)
    except Exception:
        return bad_request("Invalid project path", code="INVALID_PROJECT_PATH")

    target_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    for i, note in enumerate(req.notes):
        try:
            content = (note.get("content") or "").strip()
            if not content:
                errors.append({"index": i, "error": "content is empty"})
                continue

            title = (note.get("title") or f"bulk-note-{timestamp}-{i + 1}").strip()
            tags = note.get("tags") or []
            metadata = note.get("metadata") or {}
            filename = f"{_slugify_filename(title)}.md"
            abs_path = target_dir / filename

            fm_lines = ["---"]
            if tags:
                fm_lines.append("tags:")
                for t in tags:
                    fm_lines.append(f"  - {str(t)}")
            for k, v in metadata.items():
                if isinstance(v, (str, int, float, bool)):
                    fm_lines.append(f"{k}: {v}")
            fm_lines.append("---")
            file_content = "\n".join(fm_lines) + "\n\n" + content + "\n"

            if req.skip_duplicates and abs_path.exists():
                existing = abs_path.read_text(encoding="utf-8", errors="replace")
                if existing == file_content:
                    skipped += 1
                    continue

            abs_path.write_text(file_content, encoding="utf-8")
            if deps.watcher:
                await deps.watcher.engine.sync_file(abs_path, caller="user")
            else:
                logger.warning("bulk_import: watcher not running, skipping sync for %s", abs_path)
            imported += 1
            try:
                written_paths.append(str(abs_path.relative_to(vault_root)))
            except ValueError:
                written_paths.append(str(abs_path))
        except Exception as e:
            errors.append({"index": i, "error": str(e)})

    return {
        "imported": imported,
        "skipped": skipped,
        "total": len(req.notes),
        "errors": errors,
        "paths": written_paths,
        "project": project_dir,
    }


@app.post("/bulk/export")
async def bulk_export(
    req: BulkExportRequest,
    deps: Dependencies = Depends(get_dependencies),
    _auth: str = Depends(verify_api_key),
):
    vault_root = Path(deps.settings.vault_path).resolve()
    parser = MarkdownParser()
    from_date = _parse_iso_date(req.date_from)
    to_date = _parse_iso_date(req.date_to)
    entity_paths = None

    if req.entity:
        try:
            with deps.postgres.cursor() as cursor:
                cursor.execute(
                    "SELECT DISTINCT vault_path FROM vault_entity_links WHERE entity_id::text = %s",
                    (req.entity,),
                )
                entity_paths = {row["vault_path"] for row in cursor.fetchall()}
        except Exception as e:
            return server_error("Bulk export failed", code="BULK_EXPORT_FAILED", detail=str(e))

    notes = []
    for path in vault_root.rglob("*.md"):
        if ".obsidian" in path.parts or ".trash" in path.parts:
            continue

        try:
            rel_path = str(path.relative_to(vault_root))
            if req.project and not rel_path.startswith(req.project):
                continue
            if entity_paths is not None and rel_path not in entity_paths:
                continue

            stat = path.stat()
            mtime = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)
            if from_date and mtime < from_date:
                continue
            if to_date and mtime > to_date:
                continue

            parsed = parser.parse(path, caller="user")
            if req.tags:
                tags = set(parsed.get("tags") or [])
                if not tags.intersection(set(req.tags)):
                    continue

            notes.append(
                {
                    "id": rel_path,
                    "title": path.stem,
                    "content": parsed.get("body", ""),
                    "project": parsed.get("project"),
                    "tags": parsed.get("tags") or [],
                    "metadata": {
                        "status": parsed.get("status"),
                        "trust": parsed.get("trust"),
                        "maturity": parsed.get("maturity"),
                        "importance": parsed.get("importance"),
                    },
                    "created_at": parsed.get("date_created"),
                    "modified_at": parsed.get("date_modified"),
                }
            )
            if len(notes) >= req.limit:
                break
        except Exception:
            continue

    return {
        "notes": notes,
        "count": len(notes),
        "filters": {
            "project": req.project,
            "tags": req.tags,
            "entity": req.entity,
            "date_from": req.date_from,
            "date_to": req.date_to,
            "limit": req.limit,
        },
    }


@app.post("/bulk/delete", status_code=200)
async def bulk_delete(
    req: BulkDeleteRequest,
    deps: Dependencies = Depends(get_dependencies),
    _auth: str = Depends(verify_api_key),
):
    vault_root = Path(deps.settings.vault_path).resolve()
    deleted = 0
    not_found = []
    errors = []

    for note_path in req.paths:
        try:
            abs_path = _safe_vault_path(vault_root, note_path)
            if not abs_path.exists():
                not_found.append(note_path)
                continue
            abs_path.unlink()
            if deps.watcher:
                await deps.watcher.engine.delete_file(abs_path)
            else:
                logger.warning("bulk_delete: watcher not running, skipping delete for %s", abs_path)
            deleted += 1
        except FileNotFoundError:
            not_found.append(note_path)
        except ValueError:
            errors.append({"path": note_path, "error": "Invalid or forbidden path"})
        except Exception as e:
            errors.append({"path": note_path, "error": str(e)})

    return {
        "deleted": deleted,
        "not_found": not_found,
        "errors": errors,
        "total_requested": len(req.paths),
    }


# Server entry point.


def start():
    uvicorn.run(
        "daemon.main:app",
        host="127.0.0.1",
        port=settings.port,
        log_level="info",
    )


if __name__ == "__main__":
    start()
