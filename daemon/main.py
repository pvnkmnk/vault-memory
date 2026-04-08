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
import requests
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import List, Optional

import uvicorn
import uuid
from contextvars import ContextVar
from fastapi import FastAPI, HTTPException, Request, Depends, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.middleware.base import BaseHTTPMiddleware
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
    """Dependency that verifies the API key from request headers."""
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

    if x_api_key != expected_key:
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


app = FastAPI(title="vault-memoryd", lifespan=lifespan)
app.add_middleware(CorrelationMiddleware)
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


class SessionPatchRequest(BaseModel):
    status: Optional[str] = None
    closed_at: Optional[str] = None
    notes: Optional[str] = None


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
