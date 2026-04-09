"""Pytest configuration and fixtures for vault-memory tests.

This module provides fixtures that mock heavy dependencies (sentence_transformers,
psycopg2) so tests can run without all production dependencies installed.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest


# Mock heavy dependencies before any imports
@pytest.fixture(scope="session", autouse=True)
def mock_heavy_dependencies():
    """Mock heavy ML and database dependencies for all tests."""
    # Create mock modules
    mock_sentence_transformers = MagicMock()
    mock_sentence_transformers.SentenceTransformer = MagicMock
    mock_sentence_transformers.CrossEncoder = MagicMock
    
    mock_psycopg2 = MagicMock()
    mock_psycopg2.pool = MagicMock()
    mock_psycopg2.pool.ThreadedConnectionPool = MagicMock
    mock_psycopg2.extras = MagicMock()
    mock_psycopg2.extras.RealDictCursor = MagicMock
    mock_psycopg2.Error = Exception
    mock_psycopg2.OperationalError = Exception
    mock_psycopg2.InterfaceError = Exception
    
    # Install mocks in sys.modules
    sys.modules["sentence_transformers"] = mock_sentence_transformers
    sys.modules["psycopg2"] = mock_psycopg2
    sys.modules["psycopg2.pool"] = mock_psycopg2.pool
    sys.modules["psycopg2.extras"] = mock_psycopg2.extras
    
    yield
    
    # Cleanup (optional - usually not needed for test session)
    for mod in ["sentence_transformers", "psycopg2", "psycopg2.pool", "psycopg2.extras"]:
        if mod in sys.modules and isinstance(sys.modules[mod], MagicMock):
            del sys.modules[mod]


@pytest.fixture
def mock_home_dir(tmp_path):
    """Provide a mock home directory for tests."""
    with patch("pathlib.Path.home", return_value=tmp_path):
        yield tmp_path


@pytest.fixture
def mock_db_pool():
    """Provide a mock database connection pool."""
    pool = MagicMock()
    
    # Mock connection
    mock_conn = MagicMock()
    mock_conn.cursor.return_value = MagicMock()
    mock_conn.closed = 0
    
    # Mock pool behavior
    pool.getconn.return_value = mock_conn
    pool.putconn = MagicMock()
    pool.closeall = MagicMock()
    
    yield pool


@pytest.fixture
def mock_embedder_service():
    """Provide a mock embedder service."""
    service = MagicMock()
    service.embed_async = MagicMock(return_value=[[0.1] * 384])  # 384-dim embedding
    service.rerank_async = MagicMock(return_value=[0.9, 0.8, 0.7])
    service.embed = MagicMock(return_value=[[0.1] * 384])
    service.rerank = MagicMock(return_value=[0.9, 0.8, 0.7])
    yield service


@pytest.fixture
def mock_weaviate_client():
    """Provide a mock Weaviate client."""
    client = MagicMock()
    
    # Mock collection
    mock_collection = MagicMock()
    mock_collection.query = MagicMock()
    mock_collection.query.near_vector = MagicMock()
    mock_collection.query.near_vector.return_value = MagicMock()
    mock_collection.query.near_vector.return_value.objects = []
    
    client.collections = MagicMock()
    client.collections.get.return_value = mock_collection
    
    yield client


@pytest.fixture
def mock_pg_cursor():
    """Provide a mock PostgreSQL cursor."""
    cursor = MagicMock()
    cursor.fetchone = MagicMock(return_value=None)
    cursor.fetchall = MagicMock(return_value=[])
    cursor.execute = MagicMock()
    cursor.close = MagicMock()
    
    # Context manager support
    cursor.__enter__ = MagicMock(return_value=cursor)
    cursor.__exit__ = MagicMock(return_value=False)
    
    yield cursor


@pytest.fixture
def mock_dependencies(mock_embedder_service, mock_weaviate_client, mock_db_pool):
    """Provide a mock Dependencies container with all services."""
    from daemon.dependencies import Dependencies

    deps = MagicMock(spec=Dependencies)
    deps.embedder = mock_embedder_service
    deps.weaviate = mock_weaviate_client
    deps.postgres = mock_db_pool
    deps.watcher = None

    yield deps


@pytest.fixture
def app_client(mock_dependencies):
    """Provide a FastAPI test client with mocked dependencies."""
    from fastapi.testclient import TestClient
    from daemon.main import app
    
    # Store original state
    original_state = dict(app.state._state) if hasattr(app.state, "_state") else {}
    
    # Set mock dependencies
    app.state.embedder = mock_dependencies.embedder
    app.state.weaviate = mock_dependencies.weaviate
    app.state.postgres = mock_dependencies.postgres
    
    with TestClient(app) as client:
        yield client
    
    # Restore original state
    if hasattr(app.state, "_state"):
        app.state._state.clear()
        app.state._state.update(original_state)


@pytest.fixture
def sample_search_results():
    """Provide sample search results for testing."""
    return [
        {
            "chunk_id": "chunk_001",
            "content": "Test content about Python programming",
            "source": "test.md",
            "score": 0.95,
            "metadata": {"tags": ["python", "programming"]},
        },
        {
            "chunk_id": "chunk_002",
            "content": "Another test about async programming",
            "source": "async.md",
            "score": 0.87,
            "metadata": {"tags": ["async", "python"]},
        },
    ]


@pytest.fixture
def sample_activation_candidates():
    """Provide sample GARS activation candidates for testing."""
    return [
        {"chunk_id": "chunk_001", "recency_score": 0.9, "centrality_score": 0.8},
        {"chunk_id": "chunk_002", "recency_score": 0.7, "centrality_score": 0.9},
        {"chunk_id": "chunk_003", "recency_score": 0.8, "centrality_score": 0.7},
    ]
