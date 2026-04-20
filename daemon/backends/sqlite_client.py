# daemon/backends/sqlite_client.py
"""SQLite database backend implementation for lite mode."""

import logging
import aiosqlite
import os
from typing import Any
from contextlib import contextmanager
from pathlib import Path

from daemon.db_abstraction import DatabaseBackend

logger = logging.getLogger("vault-memoryd.sqlite")


class SqliteBackend(DatabaseBackend):
    """
    SQLite backend for lite mode.
    
    Implements DatabaseBackend interface for lite mode (no PostgreSQL, no Weaviate).
    Uses aiosqlite for async operations.
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
        self._db: aiosqlite.Connection = None
        
        # Ensure directory exists
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        
        logger.info("SQLite backend initialized at: %s", db_path)

    async def pool(self) -> Any:
        """Return the connection (pool equivalent for SQLite)."""
        return self._db

    async def connect(self) -> None:
        """Establish connection to SQLite."""
        if self._db is None:
            self._db = await aiosqlite.connect(self.db_path)
            await self._init_schema()

    async def disconnect(self) -> None:
        """Close connection."""
        if self._db:
            await self._db.close()
            self._db = None

    async def _init_schema(self) -> None:
        """Initialize SQLite schema (simplified from init_db.sql)."""
        # Create simplified schema for lite mode
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
            session_id TEXT UNIQUE NOT NULL,
            agent_name TEXT,
            project TEXT,
            task TEXT,
            started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            ended_at TIMESTAMP
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
        await self._db.executescript(schema)
        await self._db.commit()

    @contextmanager
    async def cursor(self):
        """Context manager for getting a cursor."""
        if self._db is None:
            await self.connect()
        
        cursor = await self._db.cursor()
        try:
            yield cursor
            await self._db.commit()
        except Exception as e:
            await self._db.rollback()
            logger.error("SQLite error: %s", e)
            raise
        finally:
            await cursor.close()

    async def execute(self, query: str, params: tuple = ()) -> list:
        """Execute a query and return results."""
        async with self.cursor() as cursor:
            await cursor.execute(query, params)
            return await cursor.fetchall()

    async def health_check(self) -> bool:
        """Check if database is available."""
        try:
            if self._db is None:
                await self.connect()
            async with self.cursor() as cursor:
                await cursor.execute("SELECT 1")
            return True
        except Exception as e:
            logger.warning("SQLite health check failed: %s", e)
            return False


# Backwards compatible alias
class SqliteClient(SqliteBackend):
    """Backwards compatible alias."""
    pass