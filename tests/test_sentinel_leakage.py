import os
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

# Set dummy environment variable for tests
os.environ["VAULT_MEMORY_API_KEY"] = "test-key"

from daemon.main import app, get_dependencies


def _install_mock_dependencies(mock_dependencies):
    app.dependency_overrides[get_dependencies] = lambda: mock_dependencies


def _clear_mock_dependencies():
    app.dependency_overrides.clear()


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
            _install_mock_dependencies(mock_dependencies)

            # Mock app.state to avoid lifespan-related issues
            with patch.object(app, "state", MagicMock()):
                response = client.post(
                    "/search_siblings",
                    json={"query": "test", "top_k": 5},
                    headers={"x-api-key": "test-key"}
                )

            # Restore overrides
            _clear_mock_dependencies()

            assert response.status_code == 500
            data = response.json()
            assert data["error"] == "Failed to search siblings"
            assert data["code"] == "SIBLING_SEARCH_FAILED"
            # The sensitive detail should be redacted because it's a 500 error
            assert "SENSITIVE_DB_ERROR" not in str(data)
            assert "detail" not in data

def test_cognify_leakage(mock_dependencies):
    # Force an exception in ollama extraction
    mock_dependencies.settings.ollama_url = "http://localhost:11434"
    mock_dependencies.settings.ollama_model = "llama3.2"
    with patch("daemon.routes.knowledge._extract_triples_with_ollama", side_effect=Exception("SENSITIVE_LLM_KEY leaked")):
        with patch("daemon.main.lifespan", MagicMock()):
            client = TestClient(app, raise_server_exceptions=False)
            _install_mock_dependencies(mock_dependencies)

            with patch.object(app, "state", MagicMock()):
                response = client.post(
                    "/cognify",
                    json={"text": "some text", "persist": False},
                    headers={"x-api-key": "test-key"}
                )

            _clear_mock_dependencies()

            assert response.status_code == 500
            data = response.json()
            assert data["error"] == "Cognify failed"
            assert data["code"] == "COGNIFY_FAILED"
            assert "SENSITIVE_LLM_KEY" not in str(data)


def test_cognify_persistence_leakage(mock_dependencies):
    mock_dependencies.settings.lite_mode = False
    mock_dependencies.settings.ollama_url = "http://localhost:11434"
    mock_dependencies.settings.ollama_model = "llama3.2"
    mock_dependencies.postgres.cursor.side_effect = Exception("SENSITIVE_DB_PERSIST_ERROR")
    extract_result = {
        "triples": [{"subject": "Alpha", "predicate": "USES", "object": "Beta"}],
        "invalid_triples": 0,
        "model": "llama3.2",
    }

    with patch("daemon.routes.knowledge._extract_triples_with_ollama", return_value=extract_result):
        client = TestClient(app, raise_server_exceptions=False)
        _install_mock_dependencies(mock_dependencies)

        response = client.post(
            "/cognify",
            json={"text": "Alpha uses Beta", "persist": True},
            headers={"x-api-key": "test-key"},
        )

        _clear_mock_dependencies()

        assert response.status_code == 200
        data = response.json()
        assert data["persistence"]["persisted"] is False
        assert data["persistence"]["persist_error"] == "Internal persistence error"
        assert "SENSITIVE_DB_PERSIST_ERROR" not in str(data)


def test_promote_leakage(mock_dependencies, tmp_path):
    mock_dependencies.settings.vault_path = str(tmp_path)
    mock_dependencies.settings.lite_mode = True
    mock_dependencies.embedder = None

    with patch("daemon.routes.knowledge._write_text_async", side_effect=Exception("SENSITIVE_PROMOTE_ERROR")):
        client = TestClient(app, raise_server_exceptions=False)
        _install_mock_dependencies(mock_dependencies)

        response = client.post(
            "/promote",
            json={
                "text": "test",
                "title": "Test",
                "page_type": "entity",
                "vault_path": str(tmp_path),
            },
            headers={"x-api-key": "test-key"},
        )

        _clear_mock_dependencies()

        assert response.status_code == 500
        data = response.json()
        assert data["error"] == "Promote failed"
        assert data["code"] == "PROMOTE_FAILED"
        assert "SENSITIVE_PROMOTE_ERROR" not in str(data)


def test_promote_accepts_canonically_equivalent_vault_root(mock_dependencies, tmp_path):
    mock_dependencies.settings.vault_path = str(tmp_path)
    mock_dependencies.settings.lite_mode = True
    mock_dependencies.embedder = None

    client = TestClient(app, raise_server_exceptions=False)
    _install_mock_dependencies(mock_dependencies)

    response = client.post(
        "/promote",
        json={
            "text": "test",
            "title": "Canonical Root",
            "page_type": "entity",
            "vault_path": str(tmp_path / "."),
        },
        headers={"x-api-key": "test-key"},
    )

    _clear_mock_dependencies()

    assert response.status_code == 201
