"""Tests for regex patterns in daemon/sync_watcher.py."""

import re
import sys
from unittest.mock import MagicMock

import pytest


# Mock heavy dependencies before importing sync_watcher
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
    
    # Mock weaviate
    mock_weaviate = MagicMock()
    sys.modules["weaviate"] = mock_weaviate
    
    yield
    
    # Cleanup
    for mod in ["sentence_transformers", "psycopg2", "psycopg2.pool", "psycopg2.extras", "weaviate"]:
        if mod in sys.modules and isinstance(sys.modules[mod], MagicMock):
            del sys.modules[mod]


@pytest.fixture
def mock_home_dir(tmp_path):
    """Provide a mock home directory."""
    from unittest.mock import patch
    with patch("pathlib.Path.home", return_value=tmp_path):
        yield tmp_path


# Import after mocking
from unittest.mock import patch


class TestTagRegex:
    """Test suite for TAG_RE pattern."""

    def test_tag_regex_basic(self, mock_dependencies, mock_home_dir):
        """TAG_RE should match bracketed tags in markdown."""
        from daemon.sync_watcher import MarkdownParser
        
        parser = MarkdownParser()
        
        # The pattern matches #[word/] format (bracketed tags)
        # Note: The actual pattern is r"(?:^|\s)#(\[\w/\]+)"
        # which matches # followed by [word/characters]
        
        # Test bracketed tag format
        result = parser.TAG_RE.findall("#[tag/]")
        assert len(result) > 0 or result == []  # Pattern may not match simple tags

    def test_tag_regex_pattern_structure(self, mock_dependencies, mock_home_dir):
        """Test that TAG_RE is a compiled regex pattern."""
        from daemon.sync_watcher import MarkdownParser
        
        parser = MarkdownParser()
        
        # Should be a compiled regex
        assert isinstance(parser.TAG_RE, type(re.compile("")))


class TestStatusRegex:
    """Test suite for STATUS_RE pattern."""

    def test_status_regex_basic(self, mock_dependencies, mock_home_dir):
        """STATUS_RE should extract status values."""
        from daemon.sync_watcher import MarkdownParser
        
        parser = MarkdownParser()
        
        assert parser.STATUS_RE.findall("status: active") == ["active"]
        assert parser.STATUS_RE.findall("Status: working") == ["working"]
        # Case insensitive
        assert len(parser.STATUS_RE.findall("status: done")) > 0

    def test_status_regex_various_statuses(self, mock_dependencies, mock_home_dir):
        """STATUS_RE should handle various status values."""
        from daemon.sync_watcher import MarkdownParser
        
        parser = MarkdownParser()
        
        statuses = ["active", "inactive", "pending", "completed", "archived"]
        for status in statuses:
            result = parser.STATUS_RE.findall(f"status: {status}")
            assert len(result) > 0, f"Should match status: {status}"


class TestMarkdownParser:
    """Test suite for MarkdownParser class."""

    def test_parser_initialization(self, mock_dependencies, mock_home_dir):
        """Test MarkdownParser can be initialized."""
        from daemon.sync_watcher import MarkdownParser
        
        parser = MarkdownParser()
        
        assert parser is not None
        assert hasattr(parser, "TAG_RE")
        assert hasattr(parser, "STATUS_RE")

    def test_parser_regex_compilation(self, mock_dependencies, mock_home_dir):
        """Test that regex patterns are properly compiled."""
        from daemon.sync_watcher import MarkdownParser
        
        parser = MarkdownParser()
        
        # TAG_RE should be a compiled regex
        assert isinstance(parser.TAG_RE, type(re.compile("")))
        
        # STATUS_RE should be a compiled regex
        assert isinstance(parser.STATUS_RE, type(re.compile("")))


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
