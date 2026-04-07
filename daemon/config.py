# daemon/config.py
import os
import json
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Settings:
    """
    Loads from .vault-memory.json in vault root, then env overrides.
    Priority: env vars > .vault-memory.json in vault > .vault-memory.json in home
    """
    vault_path: str = field(default_factory=lambda: os.getenv(
        "VAULT_PATH", str(Path.home() / "ObsidianVault")
    ))
    weaviate_url: str = field(default_factory=lambda: os.getenv(
        "WEAVIATE_URL", "http://localhost:8080"
    ))
    pg_connection_string: str = field(default_factory=lambda: os.getenv(
        "PG_CONNECTION_STRING",
        "dbname=vault_memory user=vault password=vault_local host=localhost"
    ))
    embedding_model: str = field(default_factory=lambda: os.getenv(
        "EMBEDDING_MODEL", "sentence-transformers/e5-large"
    ))
    reranker_model: str = field(default_factory=lambda: os.getenv(
        "RERANKER_MODEL", "mixedbread-ai/mxbai-rerank-large-v1"
    ))
    port: int = field(default_factory=lambda: int(os.getenv("VAULT_MEMORY_PORT", "5051")))
    stop_daemon_on_close: bool = False

    def __post_init__(self):
        """Load from .vault-memory.json if present (vault root or home)."""
        candidates = [
            Path(self.vault_path) / ".vault-memory.json",
            Path.home() / ".vault-memory.json",
        ]
        for config_file in candidates:
            if config_file.exists():
                with open(config_file) as f:
                    overrides = json.load(f)
                for key, val in overrides.items():
                    # snake_case normalisation
                    key = key.replace("-", "_")
                    if hasattr(self, key):
                        setattr(self, key, val)
                break
