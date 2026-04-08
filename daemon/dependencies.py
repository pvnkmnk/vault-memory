# daemon/dependencies.py
"""
Dependency Injection Container for vault-memory daemon.

Provides typed, testable dependencies using FastAPI's dependency injection system.
All services are initialized in the lifespan context and stored in app.state.
"""

from typing import Protocol, Optional
from fastapi import Request, HTTPException


# Forward references for type hints
class WeaviateClient(Protocol):
    async def ping(self) -> bool: ...
    def close(self) -> None: ...


class PostgresClient(Protocol):
    async def ping(self) -> bool: ...
    def close(self) -> None: ...


class EmbedderService(Protocol):
    async def embed_one(self, text: str) -> list: ...
    async def rerank(self, query: str, passages: list) -> list: ...


class UnifiedSearch(Protocol):
    async def search(self, query: str, **kwargs): ...


class VaultSyncWatcher(Protocol):
    async def start(self): ...
    async def stop(self): ...


class HeartbeatService(Protocol):
    async def start(self, postgres): ...
    async def stop(self): ...


class Settings(Protocol):
    port: int
    weaviate_url: str
    pg_connection_string: str
    embedding_model: str
    reranker_model: str
    vault_path: str
    heartbeat_interval_seconds: int


class Dependencies:
    """
    Dependency container that provides typed access to all services.

    Usage in FastAPI endpoints:
        @app.get("/endpoint")
        async def endpoint(deps: Dependencies = Depends(get_dependencies)):
            weaviate = deps.weaviate
            postgres = deps.postgres
    """

    def __init__(self, request: Request):
        self._request = request
        self._state = request.app.state

    @property
    def weaviate(self) -> WeaviateClient:
        """Get WeaviateClient."""
        client = getattr(self._state, "weaviate", None)
        if client is None:
            raise HTTPException(503, "Weaviate not initialized")
        return client

    @property
    def postgres(self) -> PostgresClient:
        """Get PostgresClient."""
        client = getattr(self._state, "postgres", None)
        if client is None:
            raise HTTPException(503, "Postgres not initialized")
        return client

    @property
    def embedder(self) -> EmbedderService:
        """Get EmbedderService."""
        service = getattr(self._state, "embedder", None)
        if service is None:
            raise HTTPException(503, "Embedder not initialized")
        return service

    @property
    def searcher(self) -> UnifiedSearch:
        """Get UnifiedSearch."""
        service = getattr(self._state, "searcher", None)
        if service is None:
            raise HTTPException(503, "Searcher not initialized")
        return service

    @property
    def watcher(self) -> Optional[VaultSyncWatcher]:
        """Get VaultSyncWatcher (optional - may not be running)."""
        return getattr(self._state, "watcher", None)

    @property
    def heartbeat(self) -> HeartbeatService:
        """Get HeartbeatService."""
        service = getattr(self._state, "heartbeat", None)
        if service is None:
            raise HTTPException(503, "Heartbeat not initialized")
        return service

    @property
    def settings(self) -> Settings:
        """Get Settings."""
        settings = getattr(self._state, "settings", None)
        if settings is None:
            raise HTTPException(503, "Settings not initialized")
        return settings


def get_dependencies(request: Request) -> Dependencies:
    """FastAPI dependency provider for Dependencies container."""
    return Dependencies(request)


# Legacy individual dependency functions (for backward compatibility)
def get_weaviate(request: Request):
    """Get WeaviateClient from app.state (legacy, use Dependencies instead)."""
    return Dependencies(request).weaviate


def get_postgres(request: Request):
    """Get PostgresClient from app.state (legacy, use Dependencies instead)."""
    return Dependencies(request).postgres


def get_embedder(request: Request):
    """Get EmbedderService from app.state (legacy, use Dependencies instead)."""
    return Dependencies(request).embedder


def get_searcher(request: Request):
    """Get UnifiedSearch from app.state (legacy, use Dependencies instead)."""
    return Dependencies(request).searcher


def get_watcher(request: Request):
    """Get VaultSyncWatcher from app.state (legacy, use Dependencies instead)."""
    return Dependencies(request).watcher


def get_heartbeat(request: Request):
    """Get HeartbeatService from app.state (legacy, use Dependencies instead)."""
    return Dependencies(request).heartbeat


def get_settings(request: Request):
    """Get Settings from app.state (legacy, use Dependencies instead)."""
    return Dependencies(request).settings
