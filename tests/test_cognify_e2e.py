# tests/test_cognify_e2e.py
"""End-to-end /cognify coverage: real Ollama → route → real PostgreSQL.

Runs in the CI integration job (ollama service container) and locally whenever
an Ollama server is reachable. Skips automatically when the service or the
configured model is unavailable, so the standard unit run is unaffected.
"""

import os
import socket
import uuid

import pytest

# Requires real services (and real psycopg2): without the flag, conftest's
# session-wide mocks would replace the Postgres path this test exists to verify.
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("VAULT_MEMORY_REAL_SERVICES") != "1",
        reason="requires VAULT_MEMORY_REAL_SERVICES=1 (real Postgres, not conftest mocks)",
    ),
]


def _port_open(port: int) -> bool:
    try:
        with socket.create_connection(("localhost", port), timeout=1):
            return True
    except OSError:
        return False


@pytest.fixture
def cognify_env(monkeypatch):
    """Point the daemon at the local test services and clear auth for TestClient."""
    monkeypatch.setenv("OLLAMA_URL", "http://localhost:11434")
    monkeypatch.setenv("OLLAMA_MODEL", os.environ.get("TEST_OLLAMA_MODEL", "llama3.2:1b"))
    monkeypatch.setenv(
        "PG_CONNECTION_STRING",
        "dbname=vault_memory user=vault password=vault_local host=localhost",
    )
    monkeypatch.delenv("VAULT_MEMORY_API_KEY", raising=False)
    monkeypatch.delenv("LLM_PROVIDER", raising=False)  # default provider: ollama
    # A local ~/.vault-memory.json could enable lite_mode (SQLite persistence);
    # this test exercises the Postgres persist path, so force it off.
    monkeypatch.setenv("VAULT_MEMORY_LITE", "0")


@pytest.mark.asyncio
async def test_cognify_end_to_end_with_ollama(cognify_env):
    """
    E2E: POST /cognify → real Ollama triple extraction → real Postgres persistence.

    Drives the FastAPI route (not the helper) with a real PostgresClient so the
    whole persist path (temporal_entities + relationships) is exercised.
    Requires an Ollama server with the configured model pulled (CI: llama3.2:1b).
    """
    if not _port_open(11434):
        pytest.skip("Ollama not reachable on localhost:11434")

    import httpx
    from fastapi.testclient import TestClient

    from daemon.config import Settings
    from daemon.main import app
    from daemon.pg_client import PostgresClient

    model = os.environ.get("TEST_OLLAMA_MODEL", "llama3.2:1b")

    # Guard against CI cold-pull latency: the model must already be available.
    # Check via /api/tags (no generation) so a busy server can't time out the probe.
    async with httpx.AsyncClient(timeout=5.0) as probe:
        resp = await probe.get("http://localhost:11434/api/tags")
    if resp.status_code != 200:
        pytest.skip(f"Ollama API not healthy (HTTP {resp.status_code})")
    installed = [m.get("name", "") for m in resp.json().get("models", [])]
    if model not in installed:
        pytest.skip(f"Ollama model {model!r} not installed (have: {installed})")

    pg_client = PostgresClient(
        "dbname=vault_memory user=vault password=vault_local host=localhost",
        min_connections=1,
        max_connections=2,
    )

    unique = uuid.uuid4().hex[:8]
    subject = f"E2EPerson{unique}"
    obj = f"E2EProject{unique}"

    def _cleanup():
        with pg_client.cursor() as cursor:
            cursor.execute(
                "DELETE FROM relationships WHERE source_name = %s AND target_name = %s",
                (subject, obj),
            )
            cursor.execute(
                "DELETE FROM temporal_entities WHERE entity_name IN (%s, %s)",
                (subject, obj),
            )

    _cleanup()
    app.state.postgres = pg_client
    # Settings picks up the env vars set by the cognify_env fixture.
    # We deliberately do NOT run the app lifespan: it would load the heavy
    # embedding models, which this test does not exercise. Setting the two
    # state entries the /cognify route depends on is sufficient.
    app.state.settings = Settings()

    text = (
        f"{subject} is the lead architect of {obj}. "
        f"{obj} is a data platform built by {subject}. "
        f"{subject} mentors the platform team that operates {obj}."
    )

    try:
        # No context manager: TestClient only runs the lifespan on __enter__,
        # and we bypass it on purpose (see app.state.settings above).
        client = TestClient(app)
        resp = client.post(
            "/cognify",
            json={"text": text, "persist": True},
        )

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["persistence"]["persisted"] is True, body["persistence"]
        assert body["triples"], f"no triples extracted: {body}"

        # Extraction should find the two entity names regardless of the exact
        # predicates a small model chooses.
        extracted_entities = {t["subject"] for t in body["triples"]} | {
            t["object"] for t in body["triples"]
        }
        assert subject in extracted_entities, (
            f"{subject} not in extracted entities: {extracted_entities}"
        )

        # Verify real Postgres rows landed (unique names make this race-free).
        with pg_client.cursor() as cursor:
            cursor.execute(
                "SELECT COUNT(*) AS n FROM relationships "
                "WHERE source_name = %s AND target_name = %s",
                (subject, obj),
            )
            rel_count = cursor.fetchone()["n"]
            cursor.execute(
                "SELECT COUNT(*) AS n FROM temporal_entities WHERE entity_name = ANY(%s)",
                ([subject, obj],),
            )
            entity_count = cursor.fetchone()["n"]

        assert rel_count >= 1, f"expected >=1 relationship row for {subject}->{obj}"
        assert entity_count >= 2, f"expected both entities persisted, got {entity_count}"
    finally:
        _cleanup()
        pg_client.close()
