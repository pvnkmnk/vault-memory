"""Tests for the Dependency Injection container (daemon/dependencies.py)."""

import pytest
from unittest.mock import MagicMock, patch


class TestDependenciesContainer:
    """Test suite for the Dependencies container class."""

    def test_dependencies_initialization(self):
        """Test that Dependencies container initializes with a request."""
        from daemon.dependencies import Dependencies
        
        # Create mock request
        mock_request = MagicMock()
        mock_request.app.state = MagicMock()
        mock_request.app.state.weaviate = MagicMock()
        mock_request.app.state.postgres = MagicMock()
        mock_request.app.state.embedder = MagicMock()
        
        deps = Dependencies(mock_request)
        
        # Should be able to create instance
        assert deps is not None
        assert deps._request is mock_request

    def test_dependencies_with_mock_services(self):
        """Test Dependencies with mock services injected."""
        from daemon.dependencies import Dependencies
        
        # Create mock request with services
        mock_request = MagicMock()
        mock_request.app.state.weaviate = MagicMock()
        mock_request.app.state.postgres = MagicMock()
        mock_request.app.state.embedder = MagicMock()
        mock_request.app.state.searcher = MagicMock()
        mock_request.app.state.watcher = MagicMock()
        mock_request.app.state.heartbeat = MagicMock()
        mock_request.app.state.settings = MagicMock()
        
        deps = Dependencies(mock_request)
        
        # Verify services can be accessed
        assert deps.weaviate is mock_request.app.state.weaviate
        assert deps.postgres is mock_request.app.state.postgres
        assert deps.embedder is mock_request.app.state.embedder
        assert deps.searcher is mock_request.app.state.searcher
        assert deps.watcher is mock_request.app.state.watcher
        assert deps.heartbeat is mock_request.app.state.heartbeat
        assert deps.settings is mock_request.app.state.settings


class TestDependencyGetters:
    """Test suite for dependency getter functions."""

    def test_get_dependencies_returns_container(self):
        """Test that get_dependencies returns a Dependencies instance."""
        from daemon.dependencies import get_dependencies
        
        # Create mock request
        mock_request = MagicMock()
        mock_request.app.state = MagicMock()
        
        # Should return Dependencies instance
        deps = get_dependencies(mock_request)
        assert deps is not None
        assert hasattr(deps, 'weaviate')


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
