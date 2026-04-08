"""Tests for Dependency Injection integration patterns."""

import pytest
from unittest.mock import MagicMock


class TestDIBasic:
    """Test suite for basic DI patterns."""

    def test_dependencies_container_exists(self):
        """Test that Dependencies container can be created with request."""
        from daemon.dependencies import Dependencies
        
        # Create mock request
        mock_request = MagicMock()
        mock_request.app.state = MagicMock()
        mock_request.app.state.weaviate = MagicMock()
        mock_request.app.state.postgres = MagicMock()
        
        deps = Dependencies(mock_request)
        assert deps is not None

    def test_dependencies_can_hold_services(self):
        """Test that Dependencies can hold service references."""
        from daemon.dependencies import Dependencies
        
        # Create mock request with services
        mock_request = MagicMock()
        mock_request.app.state.embedder = MagicMock()
        mock_request.app.state.weaviate = MagicMock()
        mock_request.app.state.postgres = MagicMock()
        
        deps = Dependencies(mock_request)
        
        # Verify they can be retrieved
        assert deps.embedder is mock_request.app.state.embedder
        assert deps.weaviate is mock_request.app.state.weaviate
        assert deps.postgres is mock_request.app.state.postgres


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
