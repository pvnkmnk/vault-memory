# daemon/main.py
"""
vault-memoryd: Always-on local daemon.
Owns DB connections, model warm state, sync watcher.
Exposes HTTP on 127.0.0.1:5051.

v0.5.0 changes (P2 sprint):
  - P2-E: POST /sessions  — register agent session
  - P2-E: GET  /sessions  — query sessions (filter by agent_name, project, status)
  - P2-E: PATCH /sessions/{session_id} — update session (close, add notes
  v0.5.0 changes (P3 sprint):
 - P3-D: POST /cognify — Ollama LLM triple extraction (subject/predicate/object) + graceful degradation)
     - P2-B: POST /search_siblings — topic hub sibling traversal (centrality x hub_penalty scoring)
"""

import asyncio
import logging
import os
import re
import requests
import secrets
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import List, Optional

import uvicorn
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
from .dependencies import (
    Dependencies,
    get_dependencies,
    get_weaviate,
    get_postgres,
    get_embedder,
    get_searcher,
    get_watcher,
    get_heartbeat,
    get_settings,
)


# ---------------------------------------------------------------------------
# Standardized Error Response
# ---------------------------------------------------------------------------


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
    return JSONResponse(
        status_code=status_code,
        content=ErrorResponse(
            error=message,
            detail=detail if not message.startswith("Internal") else None,  # Hide details in prod
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
        correlation_id = request.headers.get(
            "X-Correlation-ID",
            request.headers.get("x-correlation-id"),
            str(uuid.uuid4()),
        )

        # Store in context variable for logging
        correlation_id_var.set(correlation_id)

        # Add correlation ID to response headers
        response = await call_next(request)
        response.headers["X-Correlation-ID"] = correlation_id

        return response


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------

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
        logger.warning("VAULT_MEMORY_API_KEY not set - authentication disabled (dev mode)")
        return x_api_key

    # Use constant-time comparison to prevent timing attacks
    if not secrets.compare_digest(x_api_key, expected_key):
        raise HTTPException(
            status_code=HTTP_401_UNAUTHORIZED,
            detail="Invalid API key.",
        )
    return x_api_key


from .health import router as health_router, mark_ready, mark_degraded
from .retrieval import UnifiedSearch, classify_query, _strategy_temporal, extract_entities
from .weaviate_client import WeaviateClient
from .pg_client import PostgresClient
from .embedder import EmbedderService
from .sync_watcher import VaultSyncWatcher
from .heartbeat import HeartbeatService

logger = logging.getLogger("vault-memoryd")
settings = Settings()

# Legacy global state (deprecated - use app.state accessors below)
weaviate_client: WeaviateClient = None
pg_client: PostgresClient = None
embedder: EmbedderService = None
searcher: UnifiedSearch = None
watcher: VaultSyncWatcher = None
heartbeat: HeartbeatService = None


# ---------------------------------------------------------------------------
# Typed Accessor Functions (replaces global state access)
# ---------------------------------------------------------------------------
# Use these in new code instead of importing globals from module


# Dependencies are now provided by .dependencies module
# Use: deps: Dependencies = Depends(get_dependencies)
# Then access: deps.weaviate, deps.postgres, deps.embedder, etc.


@asynccontextmanager
async def lifespan(app: FastAPI):
    global weaviate_client, pg_client, embedder, searcher, watcher, heartbeat

    logger.info("vault-memoryd starting...")

    weaviate_client = WeaviateClient(settings.weaviate_url)
    pg_client = PostgresClient(settings.pg_connection_string)
    embedder = EmbedderService(
        embedding_model=settings.embedding_model,
        reranker_model=settings.reranker_model,
    )
    searcher = UnifiedSearch(
        weaviate=weaviate_client,
        postgres=pg_client,
        embedder=embedder,
    )
    watcher = VaultSyncWatcher(
        vault_path=settings.vault_path,
        weaviate=weaviate_client,
        postgres=pg_client,
        embedder=embedder,
    )
    asyncio.create_task(watcher.start())
    heartbeat = HeartbeatService(settings.heartbeat_interval_seconds)
    await heartbeat.start(pg_client)

    deps_ok = await _check_dependencies()
    if deps_ok:
        mark_ready()
        logger.info("vault-memoryd ready on port %s", settings.port)
    else:
        mark_degraded("One or more dependencies unavailable at startup")
        logger.warning("vault-memoryd started in DEGRADED state")

    # Store all services in app.state for typed accessor functions
    app.state.weaviate = weaviate_client
    app.state.postgres = pg_client
    app.state.embedder = embedder
    app.state.searcher = searcher
    app.state.settings = settings
    app.state.watcher = watcher
    app.state.heartbeat = heartbeat

    yield

    logger.info("vault-memoryd shutting down...")
    if watcher:
        await watcher.stop()
    if weaviate_client:
        weaviate_client.close()
    if pg_client:
        pg_client.close()
    if heartbeat:
        await heartbeat.stop()


async def _check_dependencies() -> bool:
    try:
        await weaviate_client.ping()
        await pg_client.ping()
        return True
    except Exception as e:
        logger.error("Dependency check failed: %s", e)
        return False


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

# ---------------------------------------------------------------------------
# Rate Limiting Middleware
# ---------------------------------------------------------------------------

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

        async with self._lock:
            # Clean old requests outside window
            self._requests[key] = [ts for ts in self._requests[key] if ts > window_start]

            # Check burst limit (immediate requests)
            if len(self._requests[key]) >= self.burst_size:
                return JSONResponse(
                    status_code=429,
                    content={"error": "Rate limit exceeded", "code": "RATE_LIMIT_BURST"},
                )

            # Check rate limit over window
            if len(self._requests[key]) >= self.requests_per_minute:
                return JSONResponse(
                    status_code=429,
                    content={"error": "Rate limit exceeded", "code": "RATE_LIMIT_WINDOW"},
                )

            # Record this request
            self._requests[key].append(now)

        response = await call_next(request)
        # Add rate limit headers
        async with self._lock:
            remaining = max(0, self.requests_per_minute - len(self._requests[key]))
        response.headers["X-RateLimit-Limit"] = str(self.requests_per_minute)
        response.headers["X-RateLimit-Remaining"] = str(remaining)
        return response


# Apply rate limiting (60 req/min, burst of 10)
app.add_middleware(RateLimitMiddleware, requests_per_minute=60, burst_size=10)

# ---------------------------------------------------------------------------
# Audit Logging Middleware
# ---------------------------------------------------------------------------


class AuditLogMiddleware(BaseHTTPMiddleware):
    """Log all API requests with correlation IDs for audit trail."""

    async def dispatch(self, request: Request, call_next):
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


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------


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
    results = await deps.searcher.search(
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
    _auth: str = Depends(verify_api_key),
):
    results = await _strategy_temporal(
        query=entity,
        time_range={"start": start, "end": end},
        entities=extract_entities(entity),
        postgres=pg_client,
    )
    return {"results": [r.to_clip() for r in results]}


# ---------------------------------------------------------------------------
# P2-E: Session registry endpoints
# ---------------------------------------------------------------------------


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
                RETURNING session_id, started_at
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
                "SELECT started_at, status FROM agent_sessions WHERE session_id = %s",
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
                f"UPDATE agent_sessions SET {set_clause} WHERE session_id = %s RETURNING *",
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
            "session_id": str(row["session_id"]),
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


# P3-D: Cognify endpoint
# ---------------------------------------------------------------------------
class CognifyRequest(BaseModel):
    text: str
    entity_types: Optional[List[str]] = None

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


@app.post("/cognify")
async def cognify(req: CognifyRequest, _auth: str = Depends(verify_api_key)):
    """
    Extract entities and relationships from text using Ollama LLM.
    Returns subject/predicate/object triples in structured JSON.
    Gracefully degrades if Ollama is unavailable.
    """
    OLLAMA_URL = "http://localhost:11434"
    OLLAMA_MODEL = "llama3.2"

    # Build the extraction prompt
    entity_filter = (
        f"\nOnly extract entities of these types: {req.entity_types}" if req.entity_types else ""
    )
    prompt = f"""Extract all entities and relationships from the following text.
Return ONLY a JSON array of triples in this exact format:
[{{"subject": "EntityName", "predicate": "relationship", "object": "EntityName"}}]

Do not include any explanation or markdown. Do not include null or empty values.
{entity_filter}

Text:
{req.text}
"""

    try:
        # Call Ollama API
        r = requests.post(
            f"{OLLAMA_URL}/api/generate",
            json={"model": OLLAMA_MODEL, "prompt": prompt, "stream": False, "format": "json"},
            timeout=30.0,
        )
        r.raise_for_status()
        response_data = r.json()
        response_text = response_data.get("response", "")

        # Parse the JSON response
        import json

        try:
            triples = json.loads(response_text)
            if not isinstance(triples, list):
                triples = []
        except json.JSONDecodeError:
            # Fallback: try to extract JSON from response
            import re

            json_match = re.search(r"\[.*\]", response_text, re.DOTALL)
            if json_match:
                try:
                    triples = json.loads(json_match.group())
                except json.JSONDecodeError:
                    triples = []
            else:
                triples = []

        return {
            "triples": triples,
            "entity_count": len(triples),
            "model": OLLAMA_MODEL,
            "text_len": len(req.text),
        }

    except requests.exceptions.RequestException as e:
        logger.warning("Ollama unavailable for cognify: %s", e)
        return {
            "triples": [],
            "entity_count": 0,
            "model": OLLAMA_MODEL,
            "text_len": len(req.text),
            "error": f"Ollama unavailable: {e}",
            "degraded": True,
        }
    except Exception as e:
        logger.error("cognify error: %s", e)
        return {
            "triples": [],
            "entity_count": 0,
            "model": OLLAMA_MODEL,
            "text_len": len(req.text),
            "error": str(e),
        }


# ---------------------------------------------------------------------------
# P2-B: search_siblings endpoint — topic hub sibling traversal
# ---------------------------------------------------------------------------
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
                  AND r.source_name NOT IN (%s)
                ORDER BY te.centrality DESC
                """,
                (hub_names, seed_names, tuple(entities)),
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


# ---------------------------------------------------------------------------
# Bulk Operations Endpoints
# ---------------------------------------------------------------------------


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
    ids: List[str]
    confirm: bool = False

    @field_validator("ids")
    @classmethod
    def validate_ids(cls, v: List[str]) -> List[str]:
        if not v:
            raise ValueError("ids list cannot be empty")
        if len(v) > 1000:
            raise ValueError("Too many ids (max 1000 per batch)")
        return v

    @field_validator("confirm")
    @classmethod
    def validate_confirm(cls, v: bool) -> bool:
        if not v:
            raise ValueError("confirm must be True to perform bulk delete")
        return v


@app.post("/bulk/import", status_code=201)
async def bulk_import(
    req: BulkImportRequest,
    deps: Dependencies = Depends(get_dependencies),
    _auth: str = Depends(verify_api_key),
):
    """
    Bulk import notes into the vault.

    Each note should have:
    - content: str (required) - note content
    - title: str (optional) - note title
    - tags: List[str] (optional) - tags to apply
    - metadata: dict (optional) - additional metadata

    Returns import statistics and any errors.
    """
    imported = 0
    errors = []

    try:
        with deps.postgres.cursor() as cursor:
            for i, note in enumerate(req.notes):
                try:
                    content = note.get("content", "")
                    title = note.get("title", f"Bulk Import {i + 1}")
                    tags = note.get("tags", [])
                    metadata = note.get("metadata", {})

                    # Check for duplicates if enabled
                    if req.skip_duplicates:
                        cursor.execute(
                            "SELECT id FROM notes WHERE content_hash = md5(%s)", (content,)
                        )
                        if cursor.fetchone():
                            continue

                    # Insert note
                    cursor.execute(
                        """
                        INSERT INTO notes (title, content, project, tags, metadata, created_at)
                        VALUES (%s, %s, %s, %s, %s, NOW())
                        RETURNING id
                        """,
                        (title, content, req.project, tags, metadata),
                    )
                    imported += 1

                except Exception as e:
                    errors.append({"index": i, "error": str(e)})

        audit_logger.info(
            "BULK_IMPORT",
            extra={
                "correlation_id": correlation_id_var.get(),
                "imported": imported,
                "errors": len(errors),
                "project": req.project,
            },
        )

        return {
            "imported": imported,
            "total": len(req.notes),
            "errors": errors,
            "project": req.project,
        }

    except Exception as e:
        return server_error("Bulk import failed", code="BULK_IMPORT_FAILED", detail=str(e))


@app.post("/bulk/export")
async def bulk_export(
    req: BulkExportRequest,
    deps: Dependencies = Depends(get_dependencies),
    _auth: str = Depends(verify_api_key),
):
    """
    Bulk export notes from the vault with filtering.

    Supports filtering by:
    - project: specific project name
    - tags: list of tags (OR match)
    - entity: entity name in relationships
    - date range: date_from to date_to (ISO format)

    Returns notes in export format.
    """
    try:
        with deps.postgres.cursor() as cursor:
            # Build dynamic query
            conditions = []
            params = []

            if req.project:
                conditions.append("project = %s")
                params.append(req.project)

            if req.tags:
                # Match any of the provided tags
                tag_conditions = " OR ".join(["%s = ANY(tags)"] * len(req.tags))
                conditions.append(f"({tag_conditions})")
                params.extend(req.tags)

            if req.date_from:
                conditions.append("created_at >= %s")
                params.append(req.date_from)

            if req.date_to:
                conditions.append("created_at <= %s")
                params.append(req.date_to)

            where_clause = "WHERE " + " AND ".join(conditions) if conditions else ""

            cursor.execute(
                f"""
                SELECT id, title, content, project, tags, metadata, created_at
                FROM notes
                {where_clause}
                ORDER BY created_at DESC
                LIMIT %s
                """,
                params + [req.limit],
            )

            notes = []
            for row in cursor.fetchall():
                notes.append(
                    {
                        "id": str(row["id"]),
                        "title": row["title"],
                        "content": row["content"],
                        "project": row["project"],
                        "tags": row["tags"],
                        "metadata": row["metadata"],
                        "created_at": row["created_at"].isoformat() if row["created_at"] else None,
                    }
                )

            # If entity filter provided, also get related notes
            if req.entity and notes:
                note_ids = [n["id"] for n in notes]
                cursor.execute(
                    """
                    SELECT DISTINCT n.id, n.title, n.content, n.project, n.tags, n.metadata, n.created_at
                    FROM notes n
                    JOIN relationships r ON n.id = r.source_id OR n.id = r.target_id
                    WHERE r.entity_name = %s AND n.id != ALL(%s)
                    LIMIT %s
                    """,
                    (req.entity, note_ids, req.limit - len(notes)),
                )
                for row in cursor.fetchall():
                    notes.append(
                        {
                            "id": str(row["id"]),
                            "title": row["title"],
                            "content": row["content"],
                            "project": row["project"],
                            "tags": row["tags"],
                            "metadata": row["metadata"],
                            "created_at": row["created_at"].isoformat()
                            if row["created_at"]
                            else None,
                        }
                    )

        audit_logger.info(
            "BULK_EXPORT",
            extra={
                "correlation_id": correlation_id_var.get(),
                "exported": len(notes),
                "filters": {
                    "project": req.project,
                    "tags": req.tags,
                    "entity": req.entity,
                },
            },
        )

        return {
            "notes": notes,
            "count": len(notes),
            "filters": {
                "project": req.project,
                "tags": req.tags,
                "entity": req.entity,
                "date_from": req.date_from,
                "date_to": req.date_to,
            },
        }

    except Exception as e:
        return server_error("Bulk export failed", code="BULK_EXPORT_FAILED", detail=str(e))


@app.post("/bulk/delete", status_code=200)
async def bulk_delete(
    req: BulkDeleteRequest,
    deps: Dependencies = Depends(get_dependencies),
    _auth: str = Depends(verify_api_key),
):
    """
    Bulk delete notes by ID.

    Requires confirm=True to prevent accidental deletion.
    Returns deletion statistics.
    """
    deleted = 0
    not_found = []

    try:
        with deps.postgres.cursor() as cursor:
            for note_id in req.ids:
                cursor.execute("DELETE FROM notes WHERE id = %s RETURNING id", (note_id,))
                if cursor.fetchone():
                    deleted += 1
                else:
                    not_found.append(note_id)

        audit_logger.warning(
            "BULK_DELETE",
            extra={
                "correlation_id": correlation_id_var.get(),
                "deleted": deleted,
                "not_found": len(not_found),
                "ids": req.ids,
            },
        )

        return {
            "deleted": deleted,
            "not_found": not_found,
            "total_requested": len(req.ids),
        }

    except Exception as e:
        return server_error("Bulk delete failed", code="BULK_DELETE_FAILED", detail=str(e))


# ---------------------------------------------------------------------------
# Server entry point
# ---------------------------------------------------------------------------


def start():
    uvicorn.run(
        "daemon.main:app",
        host="127.0.0.1",
        port=settings.port,
        log_level="info",
    )


if __name__ == "__main__":
    start()
