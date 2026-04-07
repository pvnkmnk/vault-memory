# daemon/main.py
"""
vault-memoryd: Always-on local daemon.
Owns DB connections, model warm state, sync watcher.
Exposes HTTP on 127.0.0.1:5051.
"""

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from typing import Optional

import uvicorn
from fastapi import FastAPI, Request
from pydantic import BaseModel

from .config import Settings
from .health import router as health_router, mark_ready, mark_degraded
from .retrieval import UnifiedSearch, classify_query
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
    )
    return {
        "results": [r.to_clip() for r in results],
        "intent": classify_query(req.query).value,
    }


@app.get("/graph")
async def graph_query(entity: str, relationship: Optional[str] = None):
    cursor = pg_client.conn.cursor()
    sql = "SELECT target_name, relationship_type FROM relationships WHERE source_name = %s"
    params = [entity]
    if relationship:
        sql += " AND relationship_type = %s"
        params.append(relationship)
    cursor.execute(sql, params)
    rows = cursor.fetchall()
    cursor.close()
    return {"paths": [{"target": r[0], "relationship": r[1]} for r in rows]}


@app.get("/temporal")
async def temporal_query(
    entity: str, start: str = "2025-01-01", end: str = "2025-12-31"
):
    results = await searcher._temporal_search(
        query=entity, time_range={"start": start, "end": end}
    )
    return {"results": [r.to_clip() for r in results]}


def start():
    uvicorn.run(
        "daemon.main:app",
        host="127.0.0.1",
        port=settings.port,
        log_level="info",
    )


if __name__ == "__main__":
    start()
