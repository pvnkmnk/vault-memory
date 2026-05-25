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
from datetime import datetime, timezone
from pathlib import Path
from typing import cast

import uvicorn
from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from .config import Settings
from .dependencies import Dependencies, get_dependencies
from .dependencies import PostgresClient as PostgresProtocol
from .embedder import EmbedderService
from .circuit_breaker import CircuitBreaker, register_circuit_breaker
from .health import (
    increment_request_count,
    mark_degraded,
    mark_ready,
    set_active_sessions,
    update_dependency_status,
    update_pool_metrics,
)
from .health import router as health_router
from .heartbeat import HeartbeatService
from .retrieval import UnifiedSearch
from .sync_watcher import SyncEngine, VaultSyncWatcher
from .version import __version__
from .weaviate_client import WeaviateClient

# Import middleware
from .middleware.correlation import CorrelationMiddleware, correlation_id_var
from .middleware.security import SecurityHeadersMiddleware
from .middleware.rate_limiter import RateLimitMiddleware, rate_limiter
from .middleware.audit import AuditLogMiddleware

# Import routers
from .routes.search import search_router, search_siblings_router
from .routes.graph import graph_router
from .routes.temporal import temporal_router
from .routes.sessions import sessions_router
from .routes.knowledge import knowledge_router
from .routes.sync import sync_router
from .routes.bulk import bulk_router, _cleanup_old_jobs
from .routes.usage import usage_router

logger = logging.getLogger("vault-memoryd")
settings = Settings()


# ── Lifespan ──────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("vault-memoryd starting...")
    api_key = os.environ.get("VAULT_MEMORY_API_KEY")
    if not api_key:
        logger.warning("VAULT_MEMORY_API_KEY not set - authentication disabled (dev mode)")

    # S30-4: Create circuit breakers first (needed by services)
    embedder_cb = CircuitBreaker("embedder", failure_threshold=5, recovery_timeout=120.0)
    weaviate_cb = CircuitBreaker("weaviate", failure_threshold=5, recovery_timeout=120.0)
    ollama_cb = CircuitBreaker("ollama", failure_threshold=5, recovery_timeout=120.0)
    cbs = {"embedder": embedder_cb, "weaviate": weaviate_cb, "ollama": ollama_cb}
    for cb in cbs.values():
        register_circuit_breaker(cb)

    if settings.lite_mode:
        logger.info("Starting in LITE mode (SQLite)")
        from daemon.backends.sqlite_client import SqliteClient
        db_client = SqliteClient(settings.sqlite_db_path)
        await db_client.connect()
        weaviate_client = None
    else:
        logger.info("Starting in FULL mode (PostgreSQL + Weaviate)")
        from daemon.pg_client import PostgresClient
        db_client = PostgresClient(settings.pg_connection_string)
        weaviate_client = WeaviateClient(
            settings.weaviate_url,
            batch_concurrency=settings.weaviate_batch_concurrency,
            circuit_breaker=weaviate_cb,
        )

    embedder = EmbedderService(
        embedding_model=settings.embedding_model,
        reranker_model=settings.reranker_model,
        embed_batch_size=settings.embed_batch_size,
        circuit_breaker=embedder_cb,
    )

    if settings.lite_mode:
        searcher = None
        sync_engine = None
    else:
        weaviate_client_t = cast(WeaviateClient, weaviate_client)
        db_client_t = cast(PostgresProtocol, db_client)
        searcher = UnifiedSearch(
            weaviate=weaviate_client_t,
            postgres=db_client_t,
            embedder=embedder,
        )
        sync_engine = SyncEngine(
            vault_root=settings.vault_path,
            weaviate_client=weaviate_client_t,
            pg_client=db_client_t,
            embedder=embedder,
            sync_concurrency=settings.sync_concurrency,
            state_write_batch=settings.state_write_batch,
            state_write_timeout_s=settings.state_write_timeout_s,
        )

    watcher = None
    if sync_engine:
        watcher = VaultSyncWatcher(engine=sync_engine)
        asyncio.create_task(watcher.start())

    heartbeat = None
    if not settings.lite_mode:
        heartbeat = HeartbeatService(settings.heartbeat_interval_seconds)
        await heartbeat.start(cast(PostgresProtocol, db_client))

    app.state.weaviate = weaviate_client
    app.state.postgres = db_client
    app.state.embedder = embedder
    app.state.searcher = searcher
    app.state.settings = settings
    app.state.watcher = watcher
    app.state.heartbeat = heartbeat
    app.state.lite_mode = settings.lite_mode
    app.state.circuit_breakers = cbs

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

    try:
        start = time.time()
        await app.state.postgres.ping()
        latency = (time.time() - start) * 1000
        update_dependency_status("postgres", "healthy", latency)
        logger.info("Postgres healthy (%.2f ms)", latency)
        # S30-5: Update pool metrics
        pool_status = app.state.postgres.get_pool_status()
        update_pool_metrics("postgres", pool_status)
    except Exception as e:
        update_dependency_status("postgres", "unhealthy")
        logger.error("Postgres health check failed: %s", e)
        all_healthy = False

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

    return all_healthy


# ── App creation ──────────────────────────────────────────────────────────────

app = FastAPI(
    title="vault-memoryd",
    version=__version__,
    description="Semantic memory layer for Obsidian vaults. Exposes MCP tools + REST API for search, sync, cognify, and vault management.",
    lifespan=lifespan,
    docs_url="/docs" if os.getenv("VAULT_MEMORY_ENABLE_DOCS") else None,
    redoc_url="/redoc" if os.getenv("VAULT_MEMORY_ENABLE_DOCS") else None,
    openapi_url="/openapi.json" if os.getenv("VAULT_MEMORY_ENABLE_DOCS") else None,
)

# ── Middleware ────────────────────────────────────────────────────────────────

app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(CorrelationMiddleware)

# Rate limiting (60 req/min, burst of 20 in a 2 second window)
app.add_middleware(RateLimitMiddleware, requests_per_minute=60, burst_size=20)

app.add_middleware(AuditLogMiddleware)

# CORS
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

# ── Routes ────────────────────────────────────────────────────────────────────

app.include_router(health_router)
app.include_router(search_router)
app.include_router(search_siblings_router)
app.include_router(graph_router)
app.include_router(temporal_router)
app.include_router(sessions_router)
app.include_router(knowledge_router)
app.include_router(sync_router)
app.include_router(bulk_router)
app.include_router(usage_router)


# ── Server entry point ────────────────────────────────────────────────────────

def start():
    uvicorn.run(
        "daemon.main:app",
        host="127.0.0.1",
        port=settings.port,
        log_level="info",
    )


if __name__ == "__main__":
    start()
