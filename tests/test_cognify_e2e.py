# tests/test_cognify_e2e.py
"""End-to-end /cognify coverage: real LLM → route → real PostgreSQL.

Two provider paths are exercised, matching the daemon's provider switch:

- Ollama   (POST {url}/api/generate)   — CI service container + compose `llm` profile
- llama.cpp (POST {url}/v1/chat/completions, OpenAI-compatible json_object mode)
                                        — CI step-started llama-server + compose `llm` profile

Both tests skip automatically when their service is unreachable, so the
standard unit run is unaffected. Both require VAULT_MEMORY_REAL_SERVICES=1:
without it, conftest's session-wide psycopg2 mock would replace the Postgres
path these tests exist to verify.
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

TEST_OLLAMA_MODEL = os.environ.get("TEST_OLLAMA_MODEL", "llama3.2:1b")
TEST_LLAMACPP_MODEL = os.environ.get("TEST_LLAMACPP_MODEL", "qwen2.5-0.5b-instruct-q4_k_m")


def _port_open(port: int) -> bool:
    try:
        with socket.create_connection(("localhost", port), timeout=1):
            return True
    except OSError:
        return False


@pytest.fixture
def cognify_env(monkeypatch):
    """Point the daemon at the local Ollama test service; clear auth for TestClient."""
    monkeypatch.setenv("OLLAMA_URL", "http://localhost:11434")
    monkeypatch.setenv("OLLAMA_MODEL", TEST_OLLAMA_MODEL)
    monkeypatch.setenv(
        "PG_CONNECTION_STRING",
        "dbname=vault_memory user=vault password=vault_local host=localhost",
    )
    monkeypatch.delenv("VAULT_MEMORY_API_KEY", raising=False)
    monkeypatch.delenv("LLM_PROVIDER", raising=False)  # default provider: ollama
    # A local ~/.vault-memory.json could enable lite_mode (SQLite persistence);
    # this test exercises the Postgres persist path, so force it off.
    monkeypatch.setenv("VAULT_MEMORY_LITE", "0")


@pytest.fixture
def llamacpp_env(monkeypatch):
    """Point the daemon at the local llama.cpp (OpenAI-compatible) test service."""
    monkeypatch.setenv("LLM_PROVIDER", "llamacpp")
    monkeypatch.setenv("LLAMACPP_URL", "http://localhost:8081")
    monkeypatch.setenv("LLAMACPP_MODEL", TEST_LLAMACPP_MODEL)
    monkeypatch.setenv(
        "PG_CONNECTION_STRING",
        "dbname=vault_memory user=vault password=vault_local host=localhost",
    )
    monkeypatch.delenv("VAULT_MEMORY_API_KEY", raising=False)
    monkeypatch.setenv("VAULT_MEMORY_LITE", "0")


def _drive_cognify_and_assert_persistence():
    """POST /cognify via the FastAPI route and assert real Postgres persistence.

    Uses unique entity names so assertions are race-free against any other
    data in the shared test database. Runs the route (not the extraction
    helper) with a real PostgresClient so the whole persist path
    (temporal_entities + relationships) is exercised.

    We deliberately do NOT run the app lifespan: it would load the heavy
    embedding models, which these tests do not exercise. Setting the two
    state entries the /cognify route depends on is sufficient.
    """
    from fastapi.testclient import TestClient

    from daemon.config import Settings
    from daemon.main import app
    from daemon.pg_client import PostgresClient

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
            # Remove every edge touching either unique entity: small models may
            # emit the relationship in either direction or pair it with other
            # entities, and cleanup must be complete regardless.
            cursor.execute(
                "DELETE FROM relationships WHERE source_name = ANY(%s) OR target_name = ANY(%s)",
                ([subject, obj], [subject, obj]),
            )
            cursor.execute(
                "DELETE FROM temporal_entities WHERE entity_name IN (%s, %s)",
                (subject, obj),
            )

    _cleanup()
    app.state.postgres = pg_client
    # Settings picks up the env vars set by the provider fixture.
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

        # Persistence fidelity: every distinct triple in the response must land
        # in real Postgres exactly as persisted (predicate upper-cased). Small
        # models vary in which pairing/direction they emit, so the invariant we
        # assert is response -> DB fidelity rather than one specific pairing.
        expected_edges = {
            (t["subject"], t["object"], t["predicate"].upper())
            for t in body["triples"]
        }
        with pg_client.cursor() as cursor:
            cursor.execute(
                "SELECT source_name, target_name, relationship_type FROM relationships "
                "WHERE source_name = ANY(%s) OR target_name = ANY(%s)",
                ([subject, obj], [subject, obj]),
            )
            db_edges = {
                (row["source_name"], row["target_name"], row["relationship_type"])
                for row in cursor.fetchall()
            }
            cursor.execute(
                "SELECT COUNT(*) AS n FROM temporal_entities WHERE entity_name = ANY(%s)",
                ([subject, obj],),
            )
            entity_count = cursor.fetchone()["n"]

        missing_edges = expected_edges - db_edges
        assert not missing_edges, (
            f"persisted relationships missing response triples: {sorted(missing_edges)} "
            f"(db edges: {sorted(db_edges)})"
        )
        # Unique names guarantee no pre-existing rows were skipped, so the
        # reported insert count must exactly match every triple in the response
        # (duplicates included — the SQL dedup only covers pre-existing rows).
        assert body["persistence"]["relationships_written"] == len(body["triples"]), (
            body["persistence"]
        )
        assert entity_count >= 2, f"expected both entities persisted, got {entity_count}"
    finally:
        _cleanup()
        pg_client.close()


@pytest.mark.asyncio
async def test_cognify_end_to_end_with_ollama(cognify_env):
    """
    E2E: POST /cognify → real Ollama triple extraction → real Postgres persistence.

    Requires an Ollama server with the configured model pulled (CI: llama3.2:1b).
    """
    if not _port_open(11434):
        pytest.skip("Ollama not reachable on localhost:11434")

    import httpx

    # Guard against CI cold-pull latency: the model must already be available.
    # Check via /api/tags (no generation) so a busy server can't time out the probe.
    # Any probe failure (connection, timeout, malformed JSON) means "Ollama
    # unavailable" -> skip, matching this test's availability contract.
    try:
        async with httpx.AsyncClient(timeout=5.0) as probe:
            resp = await probe.get("http://localhost:11434/api/tags")
        if resp.status_code != 200:
            pytest.skip(f"Ollama API not healthy (HTTP {resp.status_code})")
        installed = [m.get("name", "") for m in resp.json().get("models", [])]
    except (httpx.HTTPError, ValueError) as exc:
        pytest.skip(f"Ollama API unavailable or malformed: {exc}")
    if TEST_OLLAMA_MODEL not in installed:
        pytest.skip(f"Ollama model {TEST_OLLAMA_MODEL!r} not installed (have: {installed})")

    _drive_cognify_and_assert_persistence()


@pytest.mark.asyncio
async def test_cognify_end_to_end_with_llamacpp(llamacpp_env):
    """
    E2E: POST /cognify → llama.cpp (OpenAI-compatible json_object mode) → real
    Postgres persistence.

    Requires a llama-server on localhost:8081 with a model loaded (CI starts
    one with the same Qwen GGUF as the compose `llm` profile).
    """
    if not _port_open(8081):
        pytest.skip("llama.cpp server not reachable on localhost:8081")

    import httpx

    # /health returns 200 only once a model is loaded (503 while loading).
    # Probe failures mean "llama.cpp unavailable" -> skip, matching the
    # availability contract of the Ollama E2E test.
    try:
        async with httpx.AsyncClient(timeout=5.0) as probe:
            resp = await probe.get("http://localhost:8081/health")
        if resp.status_code != 200:
            pytest.skip(f"llama.cpp not ready (HTTP {resp.status_code})")
    except (httpx.HTTPError, ValueError) as exc:
        pytest.skip(f"llama.cpp unavailable or malformed: {exc}")

    _drive_cognify_and_assert_persistence()
