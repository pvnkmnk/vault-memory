# daemon/db_abstraction.py
"""Database backend abstraction for vault-memory."""

from typing import Protocol, Any, contextmanager
from abc import abstractmethod
import logging

logger = logging.getLogger("vault-memoryd.db")


class DatabaseBackend(Protocol):
    """Abstract database backend interface."""

    @abstractmethod
    async def cursor(self) -> contextmanager:
        """Return a cursor context manager for executing queries."""
        ...

    @abstractmethod
    async def pool(self) -> Any:
        """Return the connection pool or underlying client."""
        ...

    @abstractmethod
    async def execute(self, query: str, params: tuple = ()) -> list:
        """Execute a query and return results."""
        ...

    @abstractmethod
    async def health_check(self) -> bool:
        """Check if the database is available and responsive."""
        ...


class DatabaseBackendError(Exception):
    """Base exception for database backend errors."""
    pass


class BackendNotAvailableError(DatabaseBackendError):
    """Raised when the backend is not available."""
    pass


def get_backend(backend_type: str = "postgres") -> DatabaseBackend:
    """
    Factory function to get a database backend.
    
    Args:
        backend_type: Either "postgres" or "sqlite"
    
    Returns:
        DatabaseBackend implementation
    """
    if backend_type == "sqlite":
        from daemon.backends.sqlite_client import SqliteClient
        return SqliteClient()
    else:
        from daemon.backends.postgres_client import PostgresClient
        return PostgresClient()