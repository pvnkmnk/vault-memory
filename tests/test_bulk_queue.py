"""Tests for the async bulk operations queue (VAU-26 / S26-2)."""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from daemon.config import Settings
from daemon.main import app
from daemon.routes.bulk import _bulk_jobs, _bulk_job_lock, _process_bulk_job
import daemon.main as main_module


@pytest.fixture
def client(tmp_path, monkeypatch):
    """FastAPI test client with a temporary vault and mocked heavy services."""
    original_state = dict(app.state._state) if hasattr(app.state, "_state") else {}
    original_main_settings = getattr(main_module, "settings", None)

    # Other tests may configure an API key; disable auth for these route tests
    monkeypatch.delenv("VAULT_MEMORY_API_KEY", raising=False)

    settings = Settings()
    settings.vault_path = str(tmp_path)
    settings.lite_mode = True
    main_module.settings = settings
    app.state.settings = settings
    app.state.embedder = MagicMock()
    app.state.weaviate = MagicMock()
    app.state.postgres = MagicMock()
    app.state.watcher = MagicMock()
    app.state.watcher.engine = MagicMock()
    app.state.watcher.engine.sync_file = AsyncMock()

    # Clear in-memory job store
    _bulk_jobs.clear()

    with TestClient(app) as c:
        # Lifespan sets watcher=None in lite mode; re-inject mock for route tests
        app.state.watcher = MagicMock()
        app.state.watcher.engine = MagicMock()
        app.state.watcher.engine.sync_file = AsyncMock()
        yield c

    # Restore state
    if original_main_settings is not None:
        main_module.settings = original_main_settings
    if hasattr(app.state, "_state"):
        app.state._state.clear()
        app.state._state.update(original_state)
    _bulk_jobs.clear()


def test_queue_bulk_import_returns_job_id(client):
    resp = client.post(
        "/bulk/queue",
        json={
            "notes": [
                {"title": "First", "content": "body one"},
                {"title": "Second", "content": "body two"},
            ],
            "project": "Queue Test",
        },
    )
    assert resp.status_code == 202
    data = resp.json()
    assert "job_id" in data
    assert data["status"] == "queued"
    assert data["total"] == 2


def test_bulk_status_reflects_progress(client):
    resp = client.post(
        "/bulk/queue",
        json={
            "notes": [{"title": "Note", "content": "content"}],
            "project": "Status Test",
        },
    )
    job_id = resp.json()["job_id"]

    # Allow background task to finish
    status = {}
    for _ in range(20):
        status = client.get(f"/bulk/status/{job_id}").json()
        if status.get("status") in ("done", "failed", "cancelled"):
            break
        asyncio.run(asyncio.sleep(0.05))

    assert status.get("total") == 1
    assert status.get("status") == "done"
    assert status.get("done") == 1
    assert status.get("failed") == 0


@pytest.mark.asyncio
async def test_bulk_cancel_terminates_job(tmp_path):
    """Cancel toggles the cancelled flag and the background task honours it."""
    job_id = "test-cancel-job"
    note = {"title": "Note", "content": "content"}

    async with _bulk_job_lock:
        _bulk_jobs[job_id] = {
            "status": "queued",
            "total": 1,
            "done": 0,
            "failed": 0,
            "started_at": None,
            "completed_at": None,
            "project": "Cancel Test",
            "callback_url": None,
            "errors": [],
        }

    task = asyncio.create_task(
        _process_bulk_job(job_id, [note], "Cancel Test", tmp_path, False, watcher=None)
    )

    # Mark cancelled while task is running
    async with _bulk_job_lock:
        _bulk_jobs[job_id]["cancelled"] = True

    await task

    async with _bulk_job_lock:
        job = _bulk_jobs[job_id]

    assert job["status"] == "cancelled"


def test_bulk_import_writes_files_and_triggers_sync(client, tmp_path):
    resp = client.post(
        "/bulk/queue",
        json={
            "notes": [{"title": "Synced", "content": "sync body"}],
            "project": "Sync Test",
        },
    )
    job_id = resp.json()["job_id"]

    status = {}
    for _ in range(20):
        status = client.get(f"/bulk/status/{job_id}").json()
        if status.get("status") == "done":
            break
        asyncio.run(asyncio.sleep(0.05))

    assert status.get("status") == "done"
    written = tmp_path / "Sync Test" / "Synced.md"
    assert written.exists()
    assert "sync body" in written.read_text(encoding="utf-8")
    app.state.watcher.engine.sync_file.assert_awaited()
