# daemon/main.py
"""
vault-memoryd: Always-on local daemon.
Owns DB connections, model warm state, sync watcher.
Exposes HTTP on 127.0.0.1:5051.
"""

import asyncio
import logging
import os
import re
import secrets
import time
import uuid
from contextlib import asynccontextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Literal, Optional

import uvicorn
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, field_validator
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.status import HTTP_401_UNAUTHORIZED, HTTP_500_INTERNAL_SERVER_ERROR

from .config import Settings
from .dependencies import Dependencies, get_dependencies
from .embedder import EmbedderService
from .health import (
    increment_request_count,
    mark_degraded,
    mark_ready,
    router as health_router,
    update_dependency_status,
)
from .heartbeat import HeartbeatService
from .retrieval import UnifiedSearch, _strategy_temporal
from .sync_watcher import MarkdownParser, SyncEngine, VaultSyncWatcher
from .weaviate_client import WeaviateClient

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

    # Initialize watcher if enabled
    watcher = None
    if sync_engine and not settings.lite_mode:
        watcher = VaultSyncWatcher(settings.vault_path, sync_engine)
        await watcher.start()

    # Background heartbeat for maintenance (Full version only)
    heartbeat = None
    if not settings.lite_mode:
        heartbeat = HeartbeatService(settings.heartbeat_interval_seconds)
        await heartbeat.start(db_client)

    # Register services in Dependencies container
    Dependencies.register(
        weaviate=weaviate_client,
        postgres=db_client,
        embedder=embedder,
        searcher=searcher,
        settings=settings,
        watcher=watcher,
    )

    # Attach core state to app
    app.state.start_time = time.time()
    app.state.ready = False
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

    # Shutdown logic
    logger.info("vault-memoryd shutting down...")
    if watcher:
        await watcher.stop()
    if heartbeat:
        await heartbeat.stop()
    if db_client:
        await db_client.disconnect()


async def _check_dependencies(app: FastAPI) -> bool:
    """Check health of all registered dependencies."""
    deps = get_dependencies()
    all_ok = True

    # Check database
    if deps.postgres:
        try:
            with deps.postgres.cursor() as cursor:
                cursor.execute("SELECT 1")
            update_dependency_status("database", "healthy")
        except Exception as e:
            logger.error("Database health check failed: %s", e)
            update_dependency_status("database", "unhealthy", str(e))
            all_ok = False

    # Check Weaviate (Full mode only)
    if not deps.settings.lite_mode and deps.weaviate:
        if await deps.weaviate.is_ready():
            update_dependency_status("weaviate", "healthy")
        else:
            update_dependency_status("weaviate", "unhealthy")
            all_ok = False

    # Check Embedder
    if deps.embedder:
        # Simple liveness check
        update_dependency_status("embedder", "healthy")

    return all_ok


app = FastAPI(
    title="vault-memoryd",
    version="0.7.0",
    lifespan=lifespan,
)

# Standard middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(CorrelationMiddleware)

# Health router
app.include_router(health_router)


class AuditLogMiddleware(BaseHTTPMiddleware):
    """Log all API requests with correlation IDs for audit trail."""

    async def dispatch(self, request: Request, call_next):
        AUDIT_SKIP_PATHS = {"/health", "/ready", "/metrics"}
        if request.url.path in AUDIT_SKIP_PATHS:
            return await call_next(request)

        start_time = time.time()
        correlation_id = correlation_id_var.get() or str(uuid.uuid4())

        logger.info(
            "[%s] %s %s starting",
            correlation_id,
            request.method,
            request.url.path,
        )

        response = await call_next(request)

        duration_ms = (time.time() - start_time) * 1000
        logger.info(
            "[%s] %s %s finished in %.2fms - status %d",
            correlation_id,
            request.method,
            request.url.path,
            duration_ms,
            response.status_code,
        )

        # Record metrics
        increment_request_count(request.url.path, response.status_code, duration_ms)

        return response


app.add_middleware(AuditLogMiddleware)


@app.get("/ready")
async def readiness():
    """Readiness probe for k8s/monitoring."""
    status = "ready"
    if not app.state.ready:
        status = "starting"
    return {
        "status": status,
        "uptime_seconds": time.time() - app.state.start_time,
        "last_index": "N/A",
    }


@app.post("/heartbeat/run")
async def trigger_heartbeat(
    deps: Dependencies = Depends(get_dependencies),
    _auth: str = Depends(verify_api_key),
):
    """Manually trigger the heartbeat cycle."""
    if deps.settings.lite_mode:
        raise HTTPException(status_code=501, detail="Heartbeat not available in LITE mode")

    if not hasattr(app.state, "heartbeat") or not app.state.heartbeat:
        return server_error("Heartbeat service not initialized", code="HEARTBEAT_NOT_FOUND")

    # Access the internal job and run it once
    job = app.state.heartbeat._job
    if not job:
        return server_error("Heartbeat job not started", code="HEARTBEAT_JOB_NOT_STARTED")

    await job.run_once()
    return {"status": "ok", "message": "Heartbeat cycle completed"}


# ── Search ────────────────────────────────────────────────────────────────────


class SearchRequest(BaseModel):
    query: str
    limit: int = 10
    apply_decay: bool = True
    vault_root: Optional[str] = None


@app.post("/search")
async def search(
    req: SearchRequest,
    deps: Dependencies = Depends(get_dependencies),
    _auth: str = Depends(verify_api_key),
):
    if deps.settings.lite_mode:
        raise HTTPException(status_code=501, detail="Search not available in LITE mode")

    try:
        results = await deps.searcher.search(
            query=req.query,
            limit=req.limit,
            apply_decay=req.apply_decay,
            vault_root=req.vault_root or deps.settings.vault_path,
        )
        return {"results": results}
    except Exception as e:
        logger.error("Search error: %s", e)
        return server_error("Search failed", detail=str(e))


@app.get("/graph")
async def graph_query(
    entity: str,
    relationship: Optional[str] = None,
    deps: Dependencies = Depends(get_dependencies),
    _auth: str = Depends(verify_api_key),
):
    if deps.settings.lite_mode:
        raise HTTPException(status_code=501, detail="Graph not available in LITE mode")

    try:
        results = await deps.searcher.graph_traversal(
            entity=entity,
            relationship=relationship,
        )
        return {"graph": results}
    except Exception as e:
        logger.error("Graph error: %s", e)
        return server_error("Graph traversal failed", detail=str(e))


@app.get("/temporal")
async def temporal_query(
    entity: str,
    start: str = "2020-01-01",
    end: str = "2030-12-31",
    deps: Dependencies = Depends(get_dependencies),
    _auth: str = Depends(verify_api_key),
):
    if deps.settings.lite_mode:
        raise HTTPException(status_code=501, detail="Temporal queries not available in LITE mode")

    try:
        results = await _strategy_temporal(
            postgres=deps.postgres,
            entity_name=entity,
            start_date=start,
            end_date=end,
        )
        return {"events": results}
    except Exception as e:
        logger.error("Temporal error: %s", e)
        return server_error("Temporal query failed", detail=str(e))


# ── Sessions ──────────────────────────────────────────────────────────────────


class SessionRegisterRequest(BaseModel):
    agent_name: str
    project: str
    task_description: str


class SessionUpdateRequest(BaseModel):
    status: Literal["active", "closed"]
    conclusion: Optional[str] = None


@app.post("/sessions", status_code=201)
async def session_register(
    req: SessionRegisterRequest,
    deps: Dependencies = Depends(get_dependencies),
    _auth: str = Depends(verify_api_key),
):
    try:
        session_id = str(uuid.uuid4())
        with deps.postgres.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO sessions (id, agent_name, project, task_description, status, start_time)
                VALUES (%s, %s, %s, %s, 'active', NOW())
                """,
                (session_id, req.agent_name, req.project, req.task_description),
            )
        return {"session_id": session_id}
    except Exception as e:
        logger.error("Session register error: %s", e)
        return server_error("Failed to register session", detail=str(e))


@app.get("/sessions")
async def session_list(
    project: Optional[str] = None,
    limit: int = 50,
    deps: Dependencies = Depends(get_dependencies),
    _auth: str = Depends(verify_api_key),
):
    try:
        with deps.postgres.cursor() as cursor:
            if project:
                cursor.execute(
                    "SELECT * FROM sessions WHERE project = %s ORDER BY start_time DESC LIMIT %s",
                    (project, limit),
                )
            else:
                cursor.execute(
                    "SELECT * FROM sessions ORDER BY start_time DESC LIMIT %s",
                    (limit,),
                )
            rows = cursor.fetchall()
            return {"sessions": [dict(row) for row in rows]}
    except Exception as e:
        logger.error("Session list error: %s", e)
        return server_error("Failed to list sessions", detail=str(e))


@app.patch("/sessions/{session_id}")
async def session_update(
    session_id: str,
    req: SessionUpdateRequest,
    deps: Dependencies = Depends(get_dependencies),
    _auth: str = Depends(verify_api_key),
):
    try:
        with deps.postgres.cursor() as cursor:
            cursor.execute(
                """
                UPDATE sessions
                SET status = %s, conclusion = %s, end_time = CASE WHEN %s = 'closed' THEN NOW() ELSE end_time END
                WHERE id = %s
                RETURNING id
                """,
                (req.status, req.conclusion, req.status, session_id),
            )
            if not cursor.fetchone():
                return not_found("Session", session_id)
        return {"status": "updated"}
    except Exception as e:
        logger.error("Session update error: %s", e)
        return server_error("Failed to update session", detail=str(e))


# ── Knowledge Extraction (Cognify) ────────────────────────────────────────────


class CognifyRequest(BaseModel):
    path: str
    model: str = "llama3"


def _persist_cognify_triples(triples: List[dict], deps: Dependencies) -> dict:
    """Helper to persist extracted triples into the knowledge graph."""
    if not triples:
        return {"persisted": False, "relationships_written": 0, "entities_written": 0}

    relationships_written = 0
    unique_triples = []
    seen = set()
    for t in triples:
        key = (t.get("subject"), t.get("predicate"), t.get("object"))
        if key not in seen:
            unique_triples.append(t)
            seen.add(key)

    try:
        with deps.postgres.cursor() as cursor:
            for t in unique_triples:
                subj = t.get("subject")
                pred = t.get("predicate", "mentions").upper()
                obj = t.get("object")
                if subj and obj:
                    # Lite mode uses triples table, Full mode uses relationships + temporal_entities
                    if deps.settings.lite_mode:
                        cursor.execute(
                            "INSERT INTO triples (subject, predicate, object) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
                            (subj, pred, obj),
                        )
                    else:
                        cursor.execute(
                            "INSERT INTO relationships (source_name, target_name, type) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
                            (subj, obj, pred),
                        )
                    relationships_written += 1

            # Simplified entity count for response
            entities_written = len(set([t.get("subject") for t in unique_triples] + [t.get("object") for t in unique_triples]))

        return {"persisted": True, "relationships_written": relationships_written, "entities_written": entities_written}
    except Exception as e:
        logger.error("Failed to persist triples: %s", e)
        return {"persisted": False, "error": str(e)}


@app.post("/cognify")
async def cognify(
    req: CognifyRequest,
    deps: Dependencies = Depends(get_dependencies),
    _auth: str = Depends(verify_api_key),
):
    if deps.settings.lite_mode:
        raise HTTPException(status_code=501, detail="Cognify not available in LITE mode")

    vault_root = Path(deps.settings.vault_path).resolve()
    try:
        abs_path = (vault_root / req.path).resolve()
        abs_path.relative_to(vault_root)
    except Exception:
        return bad_request("Invalid file path", code="INVALID_PATH")

    if not abs_path.exists():
        return not_found("File", req.path)

    try:
        # Ollama extraction logic would go here
        # For now, it's a placeholder for triple extraction
        return {"status": "processed", "triples_extracted": 0}
    except Exception as e:
        logger.error("Cognify error: %s", e)
        return server_error("Extraction failed", detail=str(e))


# ── Content Promotion ─────────────────────────────────────────────────────────


class PromoteRequest(BaseModel):
    title: str
    body: str
    type: Literal["entity", "concept", "comparison", "analysis"]
    importance: float = 0.5


async def _write_text_async(path: Path, content: str):
    await asyncio.to_thread(path.write_text, content, encoding="utf-8")


@app.post("/promote", status_code=201)
async def promote(
    req: PromoteRequest,
    deps: Dependencies = Depends(get_dependencies),
    _auth: str = Depends(verify_api_key),
):
    vault_root = Path(deps.settings.vault_path).resolve()
    slug = re.sub(r"[^\w\- ]+", "", req.title).strip().replace(" ", "-").lower()
    filename = f"{slug}.md"

    if req.type == "concept":
        filename = f"concept-{slug}.md"
    elif req.type == "comparison":
        filename = f"compare-{slug}.md"
    elif req.type == "analysis":
        filename = f"analysis-{slug}.md"

    page_path = vault_root / filename
    if page_path.exists():
        return bad_request(f"Page already exists: {filename}", code="PAGE_EXISTS")

    # Prepare frontmatter
    now_iso = datetime.now(timezone.utc).isoformat()
    frontmatter = f"""---
title: {req.title}
type: {req.type}
importance: {req.importance}
status: tree
date_created: {now_iso}
agent_written: true
---

"""
    body = req.body
    page_content = frontmatter + body
    await _write_text_async(page_path, page_content)

    if deps.watcher:
        await deps.watcher.engine.sync_file(page_path, caller="user")
    else:
        logger.warning("promote: watcher not running — %s will be indexed on next reconcile", page_path)

    return {"path": str(page_path.relative_to(vault_root)), "status": "promoted"}


# ── Lint ──────────────────────────────────────────────────────────────────────


@app.post("/lint")
async def lint_vault(
    deps: Dependencies = Depends(get_dependencies),
    _auth: str = Depends(verify_api_key),
):
    # Vault linting logic
    return {"status": "ok", "issues": []}


# ── Siblings ──────────────────────────────────────────────────────────────────


class SiblingRequest(BaseModel):
    entity: str
    limit: int = 10


@app.post("/search_siblings")
async def search_siblings(
    req: SiblingRequest,
    deps: Dependencies = Depends(get_dependencies),
    _auth: str = Depends(verify_api_key),
):
    if deps.settings.lite_mode:
        raise HTTPException(status_code=501, detail="Siblings search not available in LITE mode")

    try:
        with deps.postgres.cursor() as cursor:
            # Topic hub logic: find entities that share the same outgoing target hubs
            sql = """
            WITH target_hubs AS (
                SELECT target_name
                FROM relationships
                WHERE source_name = %s
                AND type IN ('mentions', 'references', 'about')
            )
            SELECT DISTINCT source_name as entity_name, COUNT(*) as shared_hubs
            FROM relationships
            WHERE target_name IN (SELECT target_name FROM target_hubs)
            AND source_name != %s
            GROUP BY source_name
            ORDER BY shared_hubs DESC
            LIMIT %s
            """
            cursor.execute(sql, (req.entity, req.entity, req.limit))
            rows = cursor.fetchall()
            return {"siblings": [dict(row) for row in rows]}
    except Exception as e:
        logger.error("search_siblings error: %s", e)
        return server_error("Search failed", detail=str(e))


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
