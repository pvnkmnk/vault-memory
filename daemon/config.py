# daemon/config.py
import os
import json
from pathlib import Path
from dataclasses import dataclass, field


@dataclass
class Settings:
    vault_path: str = field(default_factory=lambda: os.getenv(
        "VAULT_PATH", str(Path.home() / "ObsidianVault")
    ))
    weaviate_url: str = field(default_factory=lambda: os.getenv(
        "WEAVIATE_URL", "http://localhost:8080"
    ))
    pg_connection_string: str = field(default_factory=lambda: os.getenv(
        "PG_CONNECTION_STRING",
        "dbname=vault_memory user=vault password=vault_local host=localhost",
    ))
    embedding_model: str = field(default_factory=lambda: os.getenv(
        "EMBEDDING_MODEL", "sentence-transformers/e5-large"
    ))
    reranker_model: str = field(default_factory=lambda: os.getenv(
        "RERANKER_MODEL", "mixedbread-ai/mxbai-rerank-large-v1"
    ))
    port: int = field(default_factory=lambda: int(os.getenv("VAULT_MEMORY_PORT", "5051")))
    heartbeat_interval_seconds: int = field(default_factory=lambda: int(os.getenv("HEARTBEAT_INTERVAL_SECONDS", "900")))

    def __post_init__(self):
        config_file = Path(self.vault_path) / ".vault-memory.json"
        if not config_file.exists():
            config_file = Path.home() / ".vault-memory.json"
        if config_file.exists():
            with open(config_file) as f:
                overrides = json.load(f)
            for key, val in overrides.items():
                if hasattr(self, key):
                    setattr(self, key, val)
