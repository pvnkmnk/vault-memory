# daemon/backends/postgres_client.py
"""PostgreSQL database backend implementation."""

import logging
import psycopg2
from psycopg2.extras import RealDictCursor
from psycopg2 import pool
from contextlib import contextmanager
from typing import Any
import time
import os

from daemon.db_abstraction import DatabaseBackend

logger = logging.getLogger("vault-memoryd.postgres")


class PostgresBackend(DatabaseBackend):
    """
    PostgreSQL backend with connection pooling.
    
    Implements DatabaseBackend interface for full mode (PostgreSQL + Weaviate).
    """

    def __init__(
        self,
        connection_string: str = None,
        min_connections: int = 2,
        max_connections: int = 10,
    ):
        if connection_string is None:
            connection_string = os.environ.get(
                "DATABASE_URL",
                "postgresql://postgres:postgres@localhost:5432/vault_memory"
            )
        
        self.connection_string = connection_string
        self.min_connections = min_connections
        self.max_connections = max_connections
        self._pool: pool.ThreadedConnectionPool = None
        self._last_health_check = 0.0
        self._health_check_interval = 30.0

        self._initialize_pool()
        logger.info("PostgreSQL backend initialized (min=%d, max=%d)", min_connections, max_connections)

    def _initialize_pool(self) -> None:
        """Initialize the connection pool."""
        self._pool = psycopg2.pool.ThreadedConnectionPool(
            minconn=self.min_connections,
            maxconn=self.max_connections,
            dsn=self.connection_string,
            cursor_factory=RealDictCursor,
        )

    async def pool(self) -> Any:
        """Return the connection pool."""
        return self._pool

    @contextmanager
    def cursor(self):
        """Context manager for getting a cursor from the pool."""
        conn = None
        cursor = None
        try:
            conn = self._pool.getconn()
            cursor = conn.cursor()
            yield cursor
            conn.commit()
        except Exception as e:
            if conn:
                conn.rollback()
            logger.error("PostgreSQL error: %s", e)
            raise
        finally:
            if cursor:
                cursor.close()
            if conn:
                self._pool.putconn(conn)

    async def execute(self, query: str, params: tuple = ()) -> list:
        """Execute a query and return results."""
        with self.cursor() as cursor:
            cursor.execute(query, params)
            return cursor.fetchall()

    async def health_check(self) -> bool:
        """Check if database is available."""
        now = time.time()
        if now - self._last_health_check < self._health_check_interval:
            return True

        self._last_health_check = now
        try:
            with self.cursor() as cursor:
                cursor.execute("SELECT 1")
            return True
        except Exception as e:
            logger.warning("PostgreSQL health check failed: %s", e)
            return False

    def close(self) -> None:
        """Close all connections."""
        if self._pool:
            self._pool.closeall()
            logger.info("PostgreSQL pool closed")


# Keep old class name for backwards compatibility
class PostgresClient(PostgresBackend):
    """Backwards compatible alias."""
    pass