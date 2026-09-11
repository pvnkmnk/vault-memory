# cli/dependencies.py
"""
Dependency Injection container for vault-memory CLI commands (S24-A3 / VAU-12).

Mirrors the daemon's `daemon/dependencies.py` pattern but for CLI commands,
which run standalone (no FastAPI app.state). Commands construct a container
instead of importing service classes directly, so tests can swap in mocks:

    deps = CliDependencies(
        weaviate=FakeWeaviate(), postgres=FakePostgres(),
        embedder=FakeEmbedder(), engine_factory=FakeEngine,
    )
    with patch("cli.sync_command.build_cli_dependencies", return_value=deps):
        runner.invoke(cli, ["sync", "--full"])

Without DI, `cli/sync_command.py` constructed real `WeaviateClient` /
`PostgresClient` / `EmbedderService` / `SyncEngine` inline in three places,
which made the sync path impossible to unit-test without live services.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, ContextManager, Optional, Protocol


class WeaviateClient(Protocol):
    async def ping(self) -> bool: ...
    def close(self) -> None: ...


class PostgresClient(Protocol):
    def cursor(self) -> ContextManager[Any]: ...
    async def ping(self) -> bool: ...
    def close(self) -> None: ...


class EmbedderService(Protocol):
    async def embed_one(self, text: str) -> list: ...
    async def rerank(self, query: str, passages: list) -> list: ...


class SyncEngine(Protocol):
    async def sync_file(self, path: Path | str, caller: str = "user") -> int: ...
    async def delete_file(self, path: Path | str) -> None: ...


# Default factory signatures matching the real daemon constructors. Kept as
# module-level callables so tests can patch a single indirection point.
def _default_weaviate(url: str):
    from daemon.weaviate_client import WeaviateClient

    return WeaviateClient(url)


def _default_postgres(conn_str: str):
    from daemon.pg_client import PostgresClient

    return PostgresClient(conn_str)


def _default_embedder(embedding_model: str, reranker_model: str):
    from daemon.embedder import EmbedderService

    return EmbedderService(embedding_model=embedding_model, reranker_model=reranker_model)


def _default_engine(vault_path: Path, weaviate, postgres, embedder):
    from daemon.sync_watcher import SyncEngine

    return SyncEngine(vault_path, weaviate, postgres, embedder)


@dataclass
class CliDependencies:
    """
    Typed service container for CLI commands.

    Accepts pre-built service instances (for tests) or constructor factories
    (for production). Services are created lazily on first access so that
    `--check-drift`-style commands never pay the model-loading cost.
    """

    vault_path: Path = field(default_factory=Path.cwd)
    weaviate_url: str = ""
    pg_conn_str: str = ""
    embedding_model: str = ""
    reranker_model: str = ""

    weaviate_factory: Callable[[str], WeaviateClient] = _default_weaviate
    postgres_factory: Callable[[str], PostgresClient] = _default_postgres
    embedder_factory: Callable[[str, str], EmbedderService] = _default_embedder
    engine_factory: Callable[[Path, Any, Any, Any], SyncEngine] = _default_engine

    _weaviate: Optional[WeaviateClient] = field(default=None, init=False, repr=False)
    _postgres: Optional[PostgresClient] = field(default=None, init=False, repr=False)
    _embedder: Optional[EmbedderService] = field(default=None, init=False, repr=False)
    _engine: Optional[SyncEngine] = field(default=None, init=False, repr=False)

    @property
    def weaviate(self) -> WeaviateClient:
        if self._weaviate is None:
            self._weaviate = self.weaviate_factory(self.weaviate_url)
        return self._weaviate

    @property
    def postgres(self) -> PostgresClient:
        if self._postgres is None:
            self._postgres = self.postgres_factory(self.pg_conn_str)
        return self._postgres

    @property
    def embedder(self) -> EmbedderService:
        if self._embedder is None:
            self._embedder = self.embedder_factory(self.embedding_model, self.reranker_model)
        return self._embedder

    @property
    def engine(self) -> SyncEngine:
        """SyncEngine wired to the other three services (built lazily)."""
        if self._engine is None:
            self._engine = self.engine_factory(
                self.vault_path, self.weaviate, self.postgres, self.embedder
            )
        return self._engine

    def close(self) -> None:
        """Release network-backed services. Safe to call multiple times."""
        for attr in ("_engine", "_weaviate", "_postgres"):
            client = getattr(self, attr, None)
            if client is not None and hasattr(client, "close"):
                try:
                    client.close()
                except Exception:  # noqa: BLE001 - close() is best-effort
                    pass
        self._engine = self._weaviate = self._postgres = None


def build_cli_dependencies(
    vault_path: Path,
    weaviate_url: str,
    pg_conn_str: str,
    embedding_model: str,
    reranker_model: str,
) -> CliDependencies:
    """Production factory used by `cli/sync_command.py`; tests patch this."""
    return CliDependencies(
        weaviate_url=weaviate_url,
        pg_conn_str=pg_conn_str,
        embedding_model=embedding_model,
        reranker_model=reranker_model,
        vault_path=vault_path,
    )


def get_pool_status(postgres: PostgresClient) -> Optional[dict]:
    """Best-effort pool stats for diagnostics (mirrors daemon pg_client API)."""
    getter = getattr(postgres, "get_pool_stats", None)
    if callable(getter):
        try:
            return getter()
        except Exception:  # noqa: BLE001
            return None
    return None


@contextmanager
def postgres_cursor(postgres: PostgresClient) -> ContextManager[Any]:
    """Context-manager cursor helper so CLI code matches daemon conventions."""
    with postgres.cursor() as cursor:
        yield cursor
