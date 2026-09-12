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


class TestMockDependenciesFidelity:
    """The conftest service doubles are spec'd against the real interfaces.

    A bare ``MagicMock`` accepts any call, so before this the unit suite could
    not notice a ``deps.*`` service call whose signature had drifted — the
    fetch-client drift had to be caught by a hand-rolled integration stub
    instead. These assertions pin the enforcement in place.
    """

    def test_postgres_double_rejects_a_drifted_keyword(self, mock_dependencies):
        with pytest.raises(TypeError):
            mock_dependencies.postgres.cursor(limit=5)

    def test_embedder_double_hides_methods_the_real_service_lacks(self, mock_dependencies):
        # EmbedderService has embed_batch/embed_one/rerank — never embed_async.
        with pytest.raises(AttributeError):
            mock_dependencies.embedder.embed_async

    def test_weaviate_double_exposes_only_the_real_api(self, mock_dependencies):
        # The upstream `collections` API does not exist on this wrapper.
        with pytest.raises(AttributeError):
            mock_dependencies.weaviate.collections

    def test_services_keep_their_configured_return_values(self, mock_dependencies):
        import asyncio

        assert asyncio.run(mock_dependencies.embedder.embed_batch(["x"])) == [[0.1] * 384]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
