"""Tests for daemon/config.py priority handling."""

import os
import sys
from pathlib import Path
from unittest.mock import patch, mock_open, MagicMock

import pytest


# Mock heavy dependencies before importing config
@pytest.fixture(autouse=True)
def mock_dependencies():
    """Mock heavy dependencies before each test."""
    # Mock sentence_transformers
    mock_st = MagicMock()
    mock_st.SentenceTransformer = MagicMock
    mock_st.CrossEncoder = MagicMock
    sys.modules["sentence_transformers"] = mock_st
    
    # Mock psycopg2
    mock_psycopg2 = MagicMock()
    mock_psycopg2.pool = MagicMock()
    mock_psycopg2.extras = MagicMock()
    sys.modules["psycopg2"] = mock_psycopg2
    sys.modules["psycopg2.pool"] = mock_psycopg2.pool
    sys.modules["psycopg2.extras"] = mock_psycopg2.extras
    
    yield
    
    # Cleanup
    for mod in ["sentence_transformers", "psycopg2", "psycopg2.pool", "psycopg2.extras"]:
        if mod in sys.modules and isinstance(sys.modules[mod], MagicMock):
            del sys.modules[mod]


@pytest.fixture
def mock_home_dir(tmp_path):
    """Provide a mock home directory."""
    with patch("pathlib.Path.home", return_value=tmp_path):
        yield tmp_path


class TestConfigPriority:
    """Test suite for configuration priority handling."""

    def test_env_vars_override_config_file(self, mock_dependencies, mock_home_dir):
        """Env vars should have highest priority, overriding config file values."""
        with patch.dict(os.environ, {"VAULT_MEMORY_PORT": "9999"}, clear=False):
            # Import after patching to get fresh Settings
            from daemon.config import Settings
            
            settings = Settings()
            assert settings.port == 9999

    def test_env_var_embeddings(self, mock_dependencies, mock_home_dir):
        """Test various env vars are read correctly."""
        with patch.dict(
            os.environ,
            {
                "WEAVIATE_URL": "http://custom:9999",
                "EMBEDDING_MODEL": "custom/model",
            },
            clear=False,
        ):
            from daemon.config import Settings
            
            settings = Settings()
            assert settings.weaviate_url == "http://custom:9999"
            assert settings.embedding_model == "custom/model"

    def test_default_values(self, mock_dependencies, mock_home_dir):
        """Test default values when no env vars set."""
        # Clear relevant env vars but keep system ones
        env_vars_to_clear = [
            "VAULT_MEMORY_PORT",
            "WEAVIATE_URL",
            "EMBEDDING_MODEL",
            "VAULT_PATH",
        ]
        
        # Get current env vars
        original_env = {k: os.environ.get(k) for k in env_vars_to_clear}
        
        try:
            # Clear the specific vars
            for var in env_vars_to_clear:
                if var in os.environ:
                    del os.environ[var]
            
            # Force reimport by clearing cache
            if "daemon.config" in sys.modules:
                del sys.modules["daemon.config"]
            
            from daemon.config import Settings
            
            settings = Settings()
            assert settings.port == 5051  # default
            assert settings.weaviate_url == "http://localhost:8080"
            
        finally:
            # Restore original env vars
            for var, value in original_env.items():
                if value is not None:
                    os.environ[var] = value


class TestConfigValidation:
    """Test suite for configuration validation."""

    def test_port_validation_positive(self, mock_dependencies, mock_home_dir):
        """Test that positive port values are accepted."""
        with patch.dict(os.environ, {"VAULT_MEMORY_PORT": "8080"}, clear=False):
            from daemon.config import Settings
            
            settings = Settings()
            assert settings.port == 8080

    def test_vault_path_default(self, mock_dependencies, mock_home_dir):
        """Test that vault path defaults to home/ObsidianVault."""
        # Clear VAULT_PATH env var
        if "VAULT_PATH" in os.environ:
            del os.environ["VAULT_PATH"]
        
        # Force reimport
        if "daemon.config" in sys.modules:
            del sys.modules["daemon.config"]
        
        from daemon.config import Settings
        
        settings = Settings()
        expected_path = mock_home_dir / "ObsidianVault"
        assert settings.vault_path == str(expected_path)


class TestConfigTypes:
    """Test suite for configuration type handling."""

    def test_port_as_string(self, mock_dependencies, mock_home_dir):
        """Test that port can be provided as string and converted to int."""
        with patch.dict(os.environ, {"VAULT_MEMORY_PORT": "9090"}, clear=False):
            from daemon.config import Settings
            
            settings = Settings()
            assert isinstance(settings.port, int)
            assert settings.port == 9090


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
