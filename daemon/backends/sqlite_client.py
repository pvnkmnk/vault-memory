"""SQLite database backend implementation for lite mode."""

import json
import logging
import os
import sqlite3
from typing import Any, Iterable
from contextlib import contextmanager
from pathlib import Path

from daemon.db_abstraction import DatabaseBackend

logger = logging.getLogger("vault-memoryd.sqlite")


class _SqliteCursorAdapter:
    """Adapt SQLite cursor behavior to the daemon's existing DB call sites."""

    def __init__(self, cursor: sqlite3.Cursor):
        self._cursor = cursor

    def execute(self, query: str, params: Iterable[Any] = ()) -> "_SqliteCursorAdapter":
        translated = query.replace("%s", "?").replace(" ILIKE ", " LIKE ")
        normalized = tuple(
            json.dumps(value) if isinstance(value, (list, dict)) else value for value in params
        )
        self._cursor.execute(translated, normalized)
        return self

    def fetchone(self):
        return self._cursor.fetchone()

    def fetchall(self):
        return self._cursor.fetchall()

    def close(self) -> None:
        self._cursor.close()


class SqliteBackend(DatabaseBackend):
    """
    SQLite backend for lite mode.

    The broader daemon still expects synchronous `with db.cursor()` usage, so this
    backend exposes a synchronous cursor context manager while keeping async
    lifecycle methods for startup and health checks.
    """

    def __init__(
        self,
        db_path: str = None,
    ):
        if db_path is None:
            db_path = os.environ.get(
                "VAULT_MEMORY_DB_PATH",
                str(Path.home() / ".vault-memory" / "lite.db")
            )

        self.db_path = db_path
        self._db: sqlite3.Connection | None = None

        # Ensure directory exists
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)

        logger.info("SQLite backend initialized at: %s", db_path)

    async def pool(self) -> Any:
        """Return the connection (pool equivalent for SQLite)."""
        return self._db

    async def connect(self) -> None:
        """Establish connection to SQLite."""
        if self._db is None:
            self._db = sqlite3.connect(self.db_path)
            self._db.row_factory = sqlite3.Row
            self._init_schema()

    async def disconnect(self) -> None:
        """Backward-compatible async close path."""
        self.close()

    def close(self) -> None:
        """Close connection."""
        if self._db:
            self._db.close()
            self._db = None

    def _init_schema(self) -> None:
        """Initialize SQLite schema for the lite-mode endpoints that remain supported."""
        if self._db is None:
            raise RuntimeError("SQLite connection is not initialized")

        schema = """
        CREATE TABLE IF NOT EXISTS files (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            file_path TEXT UNIQUE NOT NULL,
            content TEXT,
            embedding BLOB,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS agent_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agent_name TEXT,
            project TEXT,
            task TEXT,
            vault_path TEXT,
            plan_ref TEXT,
            vault_paths TEXT,
            status TEXT DEFAULT 'active',
            started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            closed_at TIMESTAMP,
            notes TEXT
        );

        CREATE TABLE IF NOT EXISTS context_blocks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            file_path TEXT NOT NULL,
            tokens INTEGER,
            content TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS triples (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            subject TEXT NOT NULL,
            predicate TEXT NOT NULL,
            object TEXT NOT NULL,
            source_file TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE INDEX IF NOT EXISTS idx_files_path ON files(file_path);
        CREATE INDEX IF NOT EXISTS idx_triples_subject ON triples(subject);
        CREATE INDEX IF NOT EXISTS idx_triples_object ON triples(object);
        """
        self._db.executescript(schema)
        self._db.commit()

    @contextmanager
    def cursor(self):
        """Context manager for getting a cursor."""
        if self._db is None:
            raise RuntimeError("SQLite connection not initialized")

        cursor = _SqliteCursorAdapter(self._db.cursor())
        try:
            yield cursor
            self._db.commit()
        except Exception as e:
            self._db.rollback()
            logger.error("SQLite error: %s", e)
            raise
        finally:
            cursor.close()

    async def execute(self, query: str, params: tuple = ()) -> list:
        """Execute a query and return results."""
        with self.cursor() as cursor:
            cursor.execute(query, params)
            return cursor.fetchall()

    async def ping(self) -> bool:
        """Check if database is available."""
        try:
            if self._db is None:
                await self.connect()
            with self.cursor() as cursor:
                cursor.execute("SELECT 1")
            return True
        except Exception as e:
            logger.warning("SQLite health check failed: %s", e)
            return False

    async def health_check(self) -> bool:
        """Backward-compatible alias for dependency checks."""
        return await self.ping()


# Backwards compatible alias
class SqliteClient(SqliteBackend):
    """Backwards compatible alias."""
    pass
