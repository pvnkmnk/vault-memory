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
"""

import asyncio
import logging
import os
import requests
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import List, Optional

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel

from .config import Settings
from .health import router as health_router, mark_ready, mark_degraded
from .retrieval import UnifiedSearch, classify_query, _strategy_temporal, extract_entities
from .weaviate_client import WeaviateClient
from .pg_client import PostgresClient
from .embedder import EmbedderService
from .sync_watcher import VaultSyncWatcher

logger = logging.getLogger("vault-memoryd")
settings = Settings()

weaviate_client: WeaviateClient = None
pg_client: PostgresClient = None
embedder: EmbedderService = None
searcher: UnifiedSearch = None
watcher: VaultSyncWatcher = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global weaviate_client, pg_client, embedder, searcher, watcher

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

    deps_ok = await _check_dependencies()
    if deps_ok:
        mark_ready()
        logger.info("vault-memoryd ready on port %s", settings.port)
    else:
        mark_degraded("One or more dependencies unavailable at startup")
        logger.warning("vault-memoryd started in DEGRADED state")

    app.state.searcher = searcher
    app.state.settings = settings

    yield

    logger.info("vault-memoryd shutting down...")
    if watcher:
        await watcher.stop()
    if weaviate_client:
        weaviate_client.close()
    if pg_client:
        pg_client.close()


async def _check_dependencies() -> bool:
    try:
        await weaviate_client.ping()
        await pg_client.ping()
        return True
    except Exception as e:
        logger.error("Dependency check failed: %s", e)
        return False


app = FastAPI(title="vault-memoryd", lifespan=lifespan)
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


@app.post("/search")
async def search(req: SearchRequest, request: Request):
    results = await request.app.state.searcher.search(
        query=req.query,
        project=req.project,
        top_k=req.top_k,
        include_graph=req.include_graph,
        include_temporal=req.include_temporal,
        time_range=req.time_range,
        vault_root=request.app.state.settings.vault_path,
    )
    return {
        "results": [r.to_clip() for r in results],
        "intent": classify_query(req.query).value,
    }


@app.get("/graph")
async def graph_query(entity: str, relationship: Optional[str] = None):
    cursor = pg_client.conn.cursor()
    sql = "SELECT target_name, relationship_type, edge_source FROM relationships WHERE source_name = %s"
    params = [entity]
    if relationship:
        sql += " AND relationship_type = %s"
        params.append(relationship)
    cursor.execute(sql, params)
    rows = cursor.fetchall()
    cursor.close()
    return {"paths": [
        {"target": r[0], "relationship": r[1], "edge_source": r[2]}
        for r in rows
    ]}


@app.get("/temporal")
async def temporal_query(
    entity: str, start: str = "2025-01-01", end: str = "2025-12-31"
):
    results = await _strategy_temporal(
        query=entity, time_range={"start": start, "end": end}, entities=extract_entities(entity), postgres=pg_client
    )
    return {"results": [r.to_clip() for r in results]}


# ---------------------------------------------------------------------------
# P2-E: Session registry endpoints
# ---------------------------------------------------------------------------

class SessionRegisterRequest(BaseModel):
    agent_name:  str
    project:     str
    task:        str
    vault_path:  str
    plan_ref:    Optional[str] = None
    vault_paths: Optional[List[str]] = None


class SessionPatchRequest(BaseModel):
    status:     Optional[str] = None
    closed_at:  Optional[str] = None
    notes:      Optional[str] = None


@app.post("/sessions", status_code=201)
async def session_register(req: SessionRegisterRequest):
    """
    Register a new agent session in agent_sessions table.
    Returns session_id and started_at.
    """
    cursor = pg_client.conn.cursor()
    try:
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
        pg_client.conn.commit()
        cursor.close()
        return {
            "session_id":  str(row["session_id"]),
            "agent_name":  req.agent_name,
            "project":     req.project,
            "task":        req.task,
            "started_at":  row["started_at"].isoformat(),
            "status":      "active",
        }
    except Exception as e:
        pg_client.conn.rollback()
        cursor.close()
        logger.error("session_register error: %s", e)
        raise HTTPException(status_code=500, detail=f"session_register failed: {e}")


@app.get("/sessions")
async def session_list(
    agent_name: Optional[str] = None,
    project:    Optional[str] = None,
    status:     Optional[str] = None,
    limit:      int = 50,
):
    """
    Query agent sessions. Supports filter by agent_name, project, status.
    """
    cursor = pg_client.conn.cursor()
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
        cursor.execute(
            f"SELECT * FROM agent_sessions {where} ORDER BY started_at DESC LIMIT %s",
            params,
        )
        rows = cursor.fetchall()
        cursor.close()
        sessions = []
        for r in rows:
            sessions.append({
                "session_id":  str(r["session_id"]),
                "agent_name":  r["agent_name"],
                "project":     r["project"],
                "task":        r["task"],
                "status":      r["status"],
                "started_at":  r["started_at"].isoformat() if r["started_at"] else None,
                "closed_at":   r["closed_at"].isoformat()  if r["closed_at"]  else None,
            })
        return {"sessions": sessions, "count": len(sessions)}
    except Exception as e:
        cursor.close()
        raise HTTPException(status_code=500, detail=f"session_list failed: {e}")


@app.patch("/sessions/{session_id}")
async def session_patch(session_id: str, req: SessionPatchRequest):
    """
    Update an agent session — typically to close it (status: closed, closed_at: now).
    Returns the updated session with duration_s calculated.
    """
    cursor = pg_client.conn.cursor()
    try:
        # Fetch existing to calculate duration
        cursor.execute(
            "SELECT started_at, status FROM agent_sessions WHERE session_id = %s",
            (session_id,),
        )
        existing = cursor.fetchone()
        if not existing:
            cursor.close()
            raise HTTPException(status_code=404, detail=f"Session {session_id} not found.")

        updates: dict = {}
        if req.status:
            updates["status"] = req.status
        if req.closed_at:
            updates["closed_at"] = req.closed_at
        if req.notes:
            updates["notes"] = req.notes

        if not updates:
            cursor.close()
            raise HTTPException(status_code=400, detail="No fields to update.")

        set_clause = ", ".join(f"{k} = %s" for k in updates)
        cursor.execute(
            f"UPDATE agent_sessions SET {set_clause} WHERE session_id = %s RETURNING *",
            list(updates.values()) + [session_id],
        )
        row = cursor.fetchone()
        pg_client.conn.commit()
        cursor.close()

        started_at = row["started_at"]
        closed_at  = row["closed_at"]
        duration_s = None
        if started_at and closed_at:
            if isinstance(closed_at, str):
                closed_at = datetime.fromisoformat(closed_at)
            try:
                duration_s = int((closed_at - started_at).total_seconds())
            except Exception:
                pass

        return {
            "session_id":  str(row["session_id"]),
            "agent_name":  row["agent_name"],
            "project":     row["project"],
            "task":        row["task"],
            "status":      row["status"],
            "started_at":  started_at.isoformat() if started_at else None,
            "closed_at":   row["closed_at"].isoformat() if row["closed_at"] else None,
            "duration_s":  duration_s,
        }
    except HTTPException:
        raise
    except Exception as e:
        pg_client.conn.rollback()
        cursor.close()
        logger.error("session_patch error: %s", e)
        raise HTTPException(status_code=500, detail=f"session_patch failed: {e}")

# P3-D: Cognify endpoint
# ---------------------------------------------------------------------------
class CognifyRequest(BaseModel):
    text: str
    entity_types: Optional[List[str]] = None

@app.post("/cognify")
async def cognify(req: CognifyRequest):
    """
    Extract entities and relationships from text using Ollama LLM.
    Returns subject/predicate/object triples in structured JSON.
    Gracefully degrades if Ollama is unavailable.
    """
    OLLAMA_URL = "http://localhost:11434"
    OLLAMA_MODEL = "llama3.2"
    
    # Build the extraction prompt
    entity_filter = f"\nOnly extract entities of these types: {req.entity_types}" if req.entity_types else ""
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
            json={
                "model": OLLAMA_MODEL,
                "prompt": prompt,
                "stream": False,
                "format": "json"
            },
            timeout=30.0
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
