from fastapi.testclient import TestClient
from unittest.mock import MagicMock, patch
import os
import pytest

# Set dummy environment variable for tests
os.environ["VAULT_MEMORY_API_KEY"] = "test-key"

from daemon.main import app, get_dependencies

def test_search_siblings_leakage(mock_dependencies):
    # Force an exception in the postgres cursor to trigger the error path
    mock_dependencies.postgres.cursor.side_effect = Exception("SENSITIVE_DB_ERROR: table 'users' not found")
    # mock_dependencies should have settings.lite_mode = False
    mock_dependencies.settings.lite_mode = False

    # Use TestClient with app.dependency_overrides to skip real init
    # And mock lifespan to avoid Weaviate connection
    with patch("daemon.main.lifespan", MagicMock()):
        with patch.object(app, "router", app.router): # dummy patch to trigger TestClient without lifespan
            client = TestClient(app, raise_server_exceptions=False)
            # Override dependency to use our mock
            app.dependency_overrides[get_dependencies] = lambda: mock_dependencies

            # Mock app.state to avoid lifespan-related issues
            with patch.object(app, "state", MagicMock()):
                response = client.post(
                    "/search_siblings",
                    json={"query": "test", "top_k": 5},
                    headers={"x-api-key": "test-key"}
                )

            # Restore overrides
            app.dependency_overrides.clear()

            assert response.status_code == 500
            data = response.json()
            assert data["error"] == "Failed to search siblings"
            # The sensitive detail should be redacted because it's a 500 error
            assert "SENSITIVE_DB_ERROR" not in data.get("detail", "")
            if "detail" in data:
                 assert data["detail"] is None or data["detail"] == ""

def test_cognify_leakage(mock_dependencies):
    # Force an exception in ollama extraction
    with patch("daemon.routes.knowledge._extract_triples_with_ollama", side_effect=Exception("SENSITIVE_LLM_KEY leaked")):
        with patch("daemon.main.lifespan", MagicMock()):
            client = TestClient(app, raise_server_exceptions=False)
            app.dependency_overrides[get_dependencies] = lambda: mock_dependencies

            with patch.object(app, "state", MagicMock()):
                response = client.post(
                    "/cognify",
                    json={"text": "some text", "persist": False},
                    headers={"x-api-key": "test-key"}
                )

            app.dependency_overrides.clear()

            # The current implementation returns 500 on extraction failure
            assert response.status_code == 500
            data = response.json()
            assert data["error"] == "Cognify failed"
            assert "SENSITIVE_LLM_KEY" not in str(data)

def test_cognify_persistence_leakage(mock_dependencies):
    # Mock extraction to succeed but persistence to fail
    extract_mock = {
        "triples": [{"subject": "a", "predicate": "b", "object": "c"}],
        "invalid_triples": 0,
        "model": "llama3.2"
    }
    with patch("daemon.routes.knowledge._extract_triples_with_ollama", return_value=extract_mock):
        mock_dependencies.postgres.cursor.side_effect = Exception("SENSITIVE_DB_PERSIST_ERROR")

        with patch("daemon.main.lifespan", MagicMock()):
            client = TestClient(app, raise_server_exceptions=False)
            app.dependency_overrides[get_dependencies] = lambda: mock_dependencies

            with patch.object(app, "state", MagicMock()):
                response = client.post(
                    "/cognify",
                    json={"text": "some text", "persist": True},
                    headers={"x-api-key": "test-key"}
                )

            app.dependency_overrides.clear()

            assert response.status_code == 200
            data = response.json()
            assert data["persistence"]["persisted"] is False
            # The sensitive detail should be redacted
            assert "SENSITIVE_DB_PERSIST_ERROR" not in str(data)
            assert data["persistence"]["persist_error"] == "Internal persistence error"

def test_promote_leakage(mock_dependencies, tmp_path):
    # Mock vault path validation to succeed
    with patch("daemon.routes.knowledge._validate_vault_root", return_value=None):
        # Force an exception in some part of promote, e.g. writing file
        with patch("daemon.routes.knowledge._write_text_async", side_effect=Exception("SENSITIVE_PROMOTE_ERROR")):
             with patch("daemon.main.lifespan", MagicMock()):
                client = TestClient(app, raise_server_exceptions=False)
                app.dependency_overrides[get_dependencies] = lambda: mock_dependencies

                with patch.object(app, "state", MagicMock()):
                    # Use a real expanduser-style path for mock vault
                    mock_dependencies.settings.vault_path = str(tmp_path)

                    response = client.post(
                        "/promote",
                        json={
                            "text": "test",
                            "title": "Test",
                            "page_type": "entity",
                            "vault_path": str(tmp_path)
                        },
                        headers={"x-api-key": "test-key"}
                    )

                app.dependency_overrides.clear()

                # If it raises uncaught exception, it should return 500
                assert response.status_code == 500
                # data = response.json() # This fails with 500 as it returns plain text for uncaught exceptions in this test setup
                assert "SENSITIVE_PROMOTE_ERROR" not in response.text
