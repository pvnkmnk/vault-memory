# daemon/config.py
import os
import json
from pathlib import Path
from dataclasses import dataclass, field


@dataclass
class Settings:
    vault_path: str = field(
        default_factory=lambda: os.getenv("VAULT_PATH", str(Path.home() / "ObsidianVault"))
    )
    weaviate_url: str = field(
        default_factory=lambda: os.getenv("WEAVIATE_URL", "http://localhost:8080")
    )
    pg_connection_string: str = field(
        default_factory=lambda: os.getenv(
            "PG_CONNECTION_STRING",
            "dbname=vault_memory user=vault password=vault_local host=localhost",
        )
    )
    embedding_model: str = field(
        default_factory=lambda: os.getenv("EMBEDDING_MODEL", "sentence-transformers/e5-large")
    )
    reranker_model: str = field(
        default_factory=lambda: os.getenv("RERANKER_MODEL", "mixedbread-ai/mxbai-rerank-large-v1")
    )
    ollama_url: str = field(default_factory=lambda: os.getenv("OLLAMA_URL", "http://localhost:11434"))
    ollama_model: str = field(default_factory=lambda: os.getenv("OLLAMA_MODEL", "llama3.2"))
    port: int = field(default_factory=lambda: int(os.getenv("VAULT_MEMORY_PORT", "5051")))
    heartbeat_interval_seconds: int = field(
        default_factory=lambda: int(os.getenv("HEARTBEAT_INTERVAL_SECONDS", "900"))
    )
    lite_mode: bool = field(
        default_factory=lambda: os.getenv("VAULT_MEMORY_LITE", "0") == "1"
    )
    sqlite_db_path: str = field(
        default_factory=lambda: os.getenv("VAULT_MEMORY_DB_PATH", str(Path.home() / ".vault-memory" / "lite.db"))
    )
    # Sync performance settings (S20)
    sync_concurrency: int = field(
        default_factory=lambda: int(os.getenv("SYNC_CONCURRENCY", "10"))
    )
    embed_batch_size: int = field(
        default_factory=lambda: int(os.getenv("EMBED_BATCH_SIZE", "64"))
    )
    state_write_batch: int = field(
        default_factory=lambda: int(os.getenv("STATE_WRITE_BATCH", "10"))
    )
    state_write_timeout_s: int = field(
        default_factory=lambda: int(os.getenv("STATE_WRITE_TIMEOUT_S", "30"))
    )
    weaviate_batch_concurrency: int = field(
        default_factory=lambda: int(os.getenv("WEAVIATE_BATCH_CONCURRENCY", "5"))
    )

    def __post_init__(self):
        # Load config file first (lower priority)
        config_file = Path(self.vault_path) / ".vault-memory.json"
        if not config_file.exists():
            config_file = Path.home() / ".vault-memory.json"
        if config_file.exists():
            with open(config_file) as f:
                overrides = json.load(f)
            for key, val in overrides.items():
                if hasattr(self, key):
                    setattr(self, key, val)
        # Env vars override config file (highest priority)
        for field_name in [
            "vault_path",
            "weaviate_url",
            "pg_connection_string",
            "embedding_model",
            "reranker_model",
            "ollama_url",
            "ollama_model",
            "port",
            "heartbeat_interval_seconds",
            # Sync performance settings (S20)
            "sync_concurrency",
            "embed_batch_size",
            "state_write_batch",
            "state_write_timeout_s",
            "weaviate_batch_concurrency",
        ]:
            env_name = field_name.upper()
            if field_name == "port":
                env_val = os.getenv("VAULT_MEMORY_PORT")
                if env_val:
                    self.port = int(env_val)
            elif field_name == "heartbeat_interval_seconds":
                env_val = os.getenv("HEARTBEAT_INTERVAL_SECONDS")
                if env_val:
                    self.heartbeat_interval_seconds = int(env_val)
            elif field_name in ("sync_concurrency", "embed_batch_size", "state_write_batch", "state_write_timeout_s", "weaviate_batch_concurrency"):
                env_val = os.getenv(field_name.upper())
                if env_val:
                    setattr(self, field_name, int(env_val))
            elif os.getenv(env_name):
                setattr(self, field_name, os.getenv(env_name))
        # Lite mode flag
        if os.getenv("VAULT_MEMORY_LITE"):
            self.lite_mode = os.getenv("VAULT_MEMORY_LITE") == "1"
