"""Tests for the PostgreSQL client (daemon/pg_client.py)."""

import pytest
from unittest.mock import MagicMock, patch


class TestPostgresClient:
    """Test suite for PostgresClient class."""

    def test_client_initialization(self):
        """Test that PostgresClient initializes properly."""
        from daemon.pg_client import PostgresClient
        
        # Mock the pool creation
        with patch("daemon.pg_client.psycopg2.pool.ThreadedConnectionPool") as mock_pool:
            mock_pool_instance = MagicMock()
            mock_pool.return_value = mock_pool_instance
            
            client = PostgresClient(
                connection_string="postgresql://user:pass@localhost/db",
                min_connections=2,
                max_connections=10,
            )
            
            # Verify pool was created
            mock_pool.assert_called_once()
            assert client._pool is mock_pool_instance

    def test_client_context_manager(self):
        """Test that PostgresClient provides cursor context manager."""
        from daemon.pg_client import PostgresClient
        
        with patch("daemon.pg_client.psycopg2.pool.ThreadedConnectionPool") as mock_pool:
            mock_pool_instance = MagicMock()
            mock_conn = MagicMock()
            mock_cursor = MagicMock()
            
            mock_pool_instance.getconn.return_value = mock_conn
            mock_conn.cursor.return_value = mock_cursor
            mock_pool.return_value = mock_pool_instance
            
            client = PostgresClient(
                connection_string="postgresql://user:pass@localhost/db",
            )
            
            # Test cursor context manager
            with client.cursor() as cursor:
                assert cursor is mock_cursor

    def test_client_transaction_context_manager(self):
        """Test that PostgresClient provides transaction context manager."""
        from daemon.pg_client import PostgresClient
        
        with patch("daemon.pg_client.psycopg2.pool.ThreadedConnectionPool") as mock_pool:
            mock_pool_instance = MagicMock()
            mock_conn = MagicMock()
            mock_cursor = MagicMock()
            
            mock_pool_instance.getconn.return_value = mock_conn
            mock_conn.cursor.return_value = mock_cursor
            mock_pool.return_value = mock_pool_instance
            
            client = PostgresClient(
                connection_string="postgresql://user:pass@localhost/db",
            )
            
            # Test transaction context manager
            with client.transaction() as cursor:
                assert cursor is mock_cursor
            
            # Verify commit was called
            mock_conn.commit.assert_called_once()

    def test_health_check(self):
        """Test that PostgresClient has health check functionality."""
        from daemon.pg_client import PostgresClient
        
        with patch("daemon.pg_client.psycopg2.pool.ThreadedConnectionPool") as mock_pool:
            mock_pool_instance = MagicMock()
            mock_conn = MagicMock()
            mock_cursor = MagicMock()
            
            mock_pool_instance.getconn.return_value = mock_conn
            mock_conn.cursor.return_value = mock_cursor
            mock_cursor.execute.return_value = None
            mock_pool.return_value = mock_pool_instance
            
            client = PostgresClient(
                connection_string="postgresql://user:pass@localhost/db",
            )
            
            # Health check should work
            result = client._health_check()
            assert result is True


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
