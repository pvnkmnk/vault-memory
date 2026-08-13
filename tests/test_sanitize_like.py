# tests/test_sanitize_like.py
import pytest
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient

from daemon.helpers.validation import sanitize_like_query
from daemon.main import app, get_dependencies

def test_sanitize_like_query_basic():
    # Test empty string
    assert sanitize_like_query("") == ""
    assert sanitize_like_query(None) == ""

    # Test normal string without any LIKE wildcards
    assert sanitize_like_query("hello world") == "hello world"

    # Test percent wildcard escaping
    assert sanitize_like_query("100% discount") == "100\\% discount"

    # Test underscore wildcard escaping
    assert sanitize_like_query("first_name") == "first\\_name"

    # Test backslash escaping
    assert sanitize_like_query("c:\\windows") == "c:\\\\windows"

    # Test mixed escaping
    assert sanitize_like_query("a_b%c\\d") == "a\\_b\\%c\\\\d"

    # Test length truncation
    long_str = "a" * 150
    truncated = sanitize_like_query(long_str, max_length=100)
    assert len(truncated) == 100
    assert truncated == "a" * 100

    # Test mix of escaping + truncation
    long_str_with_wildcard = "a" * 105 + "%"
    truncated_escaped = sanitize_like_query(long_str_with_wildcard, max_length=100)
    assert truncated_escaped == "a" * 100  # '%' is truncated out


def test_search_siblings_uses_sanitize_and_escape(mock_dependencies):
    mock_dependencies.settings.lite_mode = False

    # Mock postgres cursor and execute
    mock_cursor = MagicMock()
    mock_cursor.fetchall.return_value = [{"target_name": "sibling_one"}]
    # cursor context manager
    mock_cursor.__enter__.return_value = mock_cursor
    mock_dependencies.postgres.cursor.return_value = mock_cursor

    app.dependency_overrides[get_dependencies] = lambda: mock_dependencies

    with patch("daemon.main.lifespan", MagicMock()):
        with patch.object(app, "state", MagicMock()):
            client = TestClient(app)
            response = client.post(
                "/search_siblings",
                json={"query": "test_entity%name", "top_k": 10},
                headers={"x-api-key": "test-key"}
            )

    app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json() == {"siblings": ["sibling_one"], "count": 1}

    # Verify that execute was called with correct sanitized query parameter and ESCAPE '\'
    mock_cursor.execute.assert_called_once()
    called_sql, called_params = mock_cursor.execute.call_args[0]

    # Check that ESCAPE '\' is part of the SQL query
    assert "ESCAPE '\\'" in called_sql or "ESCAPE '\\\\'" in called_sql or r"ESCAPE '\'" in called_sql
    assert "ILIKE %s" in called_sql
    # Check that special characters '%' and '_' were escaped:
    # "test_entity%name" -> sanitized: "test\_entity\%name"
    assert called_params[0] == "%test\\_entity\\%name%"
    assert called_params[1] == 10


def test_temporal_uses_sanitize_and_escape(mock_dependencies):
    mock_dependencies.settings.lite_mode = False

    mock_cursor = MagicMock()
    mock_cursor.fetchall.return_value = [{
        "entity_name": "MyEntity",
        "date": None,
        "centrality": 0.5,
        "node_type": "entity",
        "vault_path": "path.md",
        "last_seen": None
    }]
    mock_cursor.__enter__.return_value = mock_cursor
    mock_dependencies.postgres.cursor.return_value = mock_cursor

    app.dependency_overrides[get_dependencies] = lambda: mock_dependencies

    with patch("daemon.main.lifespan", MagicMock()):
        with patch.object(app, "state", MagicMock()):
            client = TestClient(app)
            response = client.get(
                "/temporal?entity=test_entity%name",
                headers={"x-api-key": "test-key"}
            )

    app.dependency_overrides.clear()

    assert response.status_code == 200

    mock_cursor.execute.assert_called_once()
    called_sql, called_params = mock_cursor.execute.call_args[0]

    assert "ESCAPE '\\'" in called_sql or "ESCAPE '\\\\'" in called_sql or r"ESCAPE '\'" in called_sql
    assert "te.entity_name ILIKE %s" in called_sql
    assert called_params[0] == "%test\\_entity\\%name%"
