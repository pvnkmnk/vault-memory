# daemon/__init__.py
"""vault-memory daemon package."""

from .config import Settings
from .health import router as health_router, mark_ready, mark_degraded
from .retrieval import UnifiedSearch, classify_query, _strategy_temporal, extract_entities
from .weaviate_client import WeaviateClient
from .pg_client import PostgresClient
from .embedder import EmbedderService
from .sync_watcher import VaultSyncWatcher
from .heartbeat import HeartbeatService
from .dependencies import (
    Dependencies,
    get_dependencies,
)
