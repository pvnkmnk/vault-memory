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

# from .validate_write import validate_write_path  # TODO: Implement or remove
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
