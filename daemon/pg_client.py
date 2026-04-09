# daemon/pg_client.py
import logging
import psycopg2
from psycopg2.extras import RealDictCursor
from psycopg2 import pool
from contextlib import contextmanager
import time
from typing import Optional

logger = logging.getLogger("vault-memoryd.pg")


class PostgresClient:
    """
    PostgreSQL client with connection pooling and context managers.

    Features:
    - Connection pooling (min/max connections)
    - Context managers for safe cursor/transaction handling
    - Connection health checks with automatic reconnection
    - Thread-safe pool management
    """

    def __init__(
        self,
        connection_string: str,
        min_connections: int = 2,
        max_connections: int = 10,
        max_idle_time: float = 300.0,  # 5 minutes
    ):
        self.connection_string = connection_string
        self.min_connections = min_connections
        self.max_connections = max_connections
        self.max_idle_time = max_idle_time
        self._pool: Optional[pool.ThreadedConnectionPool] = None
        self._last_health_check = 0.0
        self._health_check_interval = 30.0  # Check every 30 seconds

        self._initialize_pool()
        logger.info(
            "PostgreSQL connection pool initialized (min=%d, max=%d)",
            min_connections,
            max_connections,
        )

    def _initialize_pool(self) -> None:
        """Initialize the connection pool."""
        try:
            self._pool = psycopg2.pool.ThreadedConnectionPool(
                minconn=self.min_connections,
                maxconn=self.max_connections,
                dsn=self.connection_string,
                cursor_factory=RealDictCursor,
            )
        except Exception as e:
            logger.error("Failed to initialize connection pool: %s", e)
            raise

    def _health_check(self) -> bool:
        """Check if pool is healthy, reconnect if needed."""
        now = time.time()
        if now - self._last_health_check < self._health_check_interval:
            return True

        self._last_health_check = now

        original_pool = self._pool
        conn = None
        try:
            # Try to get a connection and run a simple query
            conn = original_pool.getconn()
            with conn.cursor() as cursor:
                cursor.execute("SELECT 1")
            return True
        except Exception as e:
            logger.warning("Connection pool health check failed: %s", e)
            try:
                self._initialize_pool()
                return True
            except Exception as reinit_error:
                logger.error("Pool reinitialisation failed: %s", reinit_error)
                return False
        finally:
            if conn:
                original_pool.putconn(conn)

    @contextmanager
    def cursor(self):
        """
        Context manager for getting a cursor from the pool.

        Usage:
            with pg_client.cursor() as cursor:
                cursor.execute("SELECT * FROM table")
                rows = cursor.fetchall()
        """
        conn = None
        cursor = None
        try:
            # Health check before getting connection
            if not self._health_check():
                raise Exception("Database connection pool is not healthy")

            conn = self._pool.getconn()
            cursor = conn.cursor()
            yield cursor
            conn.commit()
        except Exception as e:
            if conn:
                conn.rollback()
            logger.error("Database error: %s", e)
            raise
        finally:
            if cursor:
                cursor.close()
            if conn:
                self._pool.putconn(conn)

    @contextmanager
    def transaction(self):
        """
        Context manager for explicit transaction handling.

        Usage:
            with pg_client.transaction() as cursor:
                cursor.execute("INSERT ...")
                cursor.execute("UPDATE ...")
                # Commits on success, rolls back on exception
        """
        with self.cursor() as cursor:
            yield cursor

    async def ping(self) -> bool:
        """Ping the database to check connectivity."""
        try:
            with self.cursor() as cursor:
                cursor.execute("SELECT 1")
                return True
        except Exception as e:
            logger.error("Database ping failed: %s", e)
            return False

    def get_pool_status(self) -> dict:
        """Get current pool status for monitoring."""
        if not self._pool:
            return {"status": "not_initialized"}

        # psycopg2 pool doesn't expose internal state directly
        # This is a best-effort status
        return {
            "status": "healthy" if self._health_check() else "unhealthy",
            "min_connections": self.min_connections,
            "max_connections": self.max_connections,
            "last_health_check": self._last_health_check,
        }

    def close(self) -> None:
        """Close all connections in the pool."""
        if self._pool:
            self._pool.closeall()
            logger.info("PostgreSQL connection pool closed")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False
