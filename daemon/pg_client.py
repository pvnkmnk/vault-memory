# daemon/pg_client.py
import logging
import psycopg2
from psycopg2.extras import RealDictCursor
from psycopg2 import pool  # type: ignore[attr-defined]
from contextlib import contextmanager
import time
from collections import deque
from typing import Any, Optional, Deque

logger = logging.getLogger("vault-memoryd.pg")

# S30-5: Slow query threshold (seconds)
SLOW_QUERY_THRESHOLD = 1.0
# S30-5: Max slow queries to keep in history
MAX_SLOW_QUERY_HISTORY = 50


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
        self._pool: Optional[Any] = None
        self._last_health_check = 0.0
        self._health_check_interval = 30.0  # Check every 30 seconds

        # S30-5: Pool metrics tracking
        self._total_queries = 0
        self._total_errors = 0
        self._slow_queries: Deque[dict] = deque(maxlen=MAX_SLOW_QUERY_HISTORY)

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
        if original_pool is None:
            return False

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
        query_start = None
        try:
            # Health check before getting connection
            if not self._health_check():
                raise Exception("Database connection pool is not healthy")

            if self._pool is None:
                raise Exception("Database connection pool is not initialized")

            conn = self._pool.getconn()
            cursor = conn.cursor()
            query_start = time.monotonic()
            yield cursor
            conn.commit()
        except Exception as e:
            if conn:
                conn.rollback()
            self._total_errors += 1
            logger.error("Database error: %s", e)
            raise
        finally:
            if cursor:
                cursor.close()
            if conn and self._pool:
                self._pool.putconn(conn)
            # S30-5: Track query timing
            if query_start is not None:
                elapsed = time.monotonic() - query_start
                self._total_queries += 1
                if elapsed > SLOW_QUERY_THRESHOLD:
                    self._record_slow_query(elapsed)

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

    def _record_slow_query(self, elapsed: float) -> None:
        """S30-5: Record a slow query for diagnostics."""
        import traceback
        self._slow_queries.append({
            "duration_seconds": round(elapsed, 3),
            "timestamp": time.time(),
            "stack_trace": "".join(traceback.format_stack()[-6:-1]).strip(),
        })
        logger.warning("Slow query detected: %.3fs", elapsed)

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
            # S30-5: Pool metrics
            "total_queries": self._total_queries,
            "total_errors": self._total_errors,
            "slow_query_count": len(self._slow_queries),
            "error_rate": round(self._total_errors / max(1, self._total_queries), 4),
        }

    def get_slow_query_diagnostics(self) -> dict:
        """S30-5: Get slow query diagnostics for performance analysis."""
        if not self._slow_queries:
            return {"slow_queries": [], "summary": {"count": 0, "avg_duration": 0, "max_duration": 0}}

        durations = [q["duration_seconds"] for q in self._slow_queries]
        return {
            "slow_queries": list(self._slow_queries),
            "summary": {
                "count": len(durations),
                "avg_duration": round(sum(durations) / len(durations), 3),
                "max_duration": round(max(durations), 3),
                "min_duration": round(min(durations), 3),
            },
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
