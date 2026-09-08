# tests/test_bulk_cleanup.py
"""Regression tests for the bulk-job cleanup single-flight pattern.

Locks down the review-bot fixes applied to ``daemon/routes/bulk.py``:

- ``_cleanup_old_jobs`` must never raise: exceptions are logged and the shared
  ``_cleanup_task`` handle is always cleared (so a failed run can re-arm).
- ``_spawn_cleanup_if_idle`` is single-flight per event loop and must not
  spawn a second task while one is in flight on the same loop.
- A stale, not-yet-done task handle from a dead loop must be discarded and
  re-armed on the current loop (``task.get_loop()`` guard).
"""

import asyncio
from unittest.mock import patch

import pytest

import daemon.routes.bulk as bulk


@pytest.fixture(autouse=True)
def _restore_module_state():
    """Save/restore module-level state touched by these tests."""
    saved_jobs = dict(bulk._bulk_jobs)
    saved_task = bulk._cleanup_task
    yield
    bulk._bulk_jobs.clear()
    bulk._bulk_jobs.update(saved_jobs)
    bulk._cleanup_task = saved_task


def _terminal_job(status="done", completed_at="2026-01-01T00:00:00+00:00"):
    return {
        "status": status,
        "total": 1,
        "done": 1,
        "failed": 0,
        "started_at": None,
        "completed_at": completed_at,
        "project": "proj",
        "callback_url": None,
        "errors": [],
    }


def test_cleanup_old_jobs_clears_task_handle_on_success():
    """The shared task handle is cleared after a successful run (re-armable)."""
    bulk._cleanup_task = "sentinel-not-a-real-task"

    asyncio.run(bulk._cleanup_old_jobs())

    assert bulk._cleanup_task is None


def test_cleanup_old_jobs_never_raises_and_clears_handle():
    """Exceptions inside the locked cleanup are logged, not propagated."""
    bulk._cleanup_task = "sentinel-not-a-real-task"

    with patch.object(bulk, "_cleanup_old_jobs_locked", side_effect=RuntimeError("boom")):
        # Must not raise even though the locked body blew up.
        asyncio.run(bulk._cleanup_old_jobs())

    assert bulk._cleanup_task is None


def test_cleanup_prunes_oldest_terminal_jobs_beyond_cap():
    """Only the oldest terminal jobs beyond the 100-job cap are pruned."""
    now = "2026-06-01T00:00:00+00:00"
    old = "2026-01-01T00:00:00+00:00"
    bulk._bulk_jobs.clear()
    # 101 terminal jobs: 1 old + 100 recent -> the old one is pruned.
    bulk._bulk_jobs["old"] = _terminal_job(completed_at=old)
    for i in range(100):
        bulk._bulk_jobs[f"recent-{i}"] = _terminal_job(completed_at=now)

    asyncio.run(bulk._cleanup_old_jobs_locked())

    assert "old" not in bulk._bulk_jobs
    assert len(bulk._bulk_jobs) == 100


def test_cleanup_never_prunes_running_or_queued_jobs():
    """Jobs still queued/processing are never removed, regardless of count."""
    now = "2026-06-01T00:00:00+00:00"
    old = "2026-01-01T00:00:00+00:00"
    bulk._bulk_jobs.clear()
    for i in range(150):
        bulk._bulk_jobs[f"queued-{i}"] = _terminal_job(status="queued", completed_at=old)
    bulk._bulk_jobs["recent-done"] = _terminal_job(completed_at=now)

    asyncio.run(bulk._cleanup_old_jobs_locked())

    assert "recent-done" in bulk._bulk_jobs
    assert sum(1 for k in bulk._bulk_jobs if k.startswith("queued-")) == 150


def test_spawn_cleanup_creates_task_when_idle():
    """No task in flight -> a new cleanup task is created."""

    async def scenario():
        bulk._cleanup_task = None
        bulk._spawn_cleanup_if_idle()
        task = bulk._cleanup_task
        assert task is not None
        await task  # let it finish cleanly

    asyncio.run(scenario())
    assert bulk._cleanup_task is None  # cleared by the coroutine's finally


def test_spawn_cleanup_is_single_flight_on_same_loop():
    """A live task on the same loop is reused, not replaced."""

    async def scenario():
        bulk._cleanup_task = None
        bulk._spawn_cleanup_if_idle()
        first = bulk._cleanup_task
        bulk._spawn_cleanup_if_idle()
        second = bulk._cleanup_task
        assert first is second
        await first

    asyncio.run(scenario())


def test_spawn_cleanup_replaces_stale_task_from_dead_loop():
    """A not-yet-done handle from a dead loop is discarded and re-armed."""

    class _FakeStaleTask:
        """Mimics a pending asyncio.Task bound to a closed (dead) loop."""

        def __init__(self, loop):
            self._loop = loop

        def done(self):
            return False

        def get_loop(self):
            return self._loop

    other_loop = asyncio.new_event_loop()
    other_loop.close()  # genuinely dead: running it would raise immediately

    async def scenario():
        stale_handle = _FakeStaleTask(other_loop)
        bulk._cleanup_task = stale_handle
        bulk._spawn_cleanup_if_idle()
        current = bulk._cleanup_task
        assert current is not stale_handle
        assert current.get_loop() is asyncio.get_running_loop()
        await current

    asyncio.run(scenario())
    assert bulk._cleanup_task is None


def test_spawn_cleanup_after_failed_run_re_arms():
    """After a failed cleanup run, the cleared handle allows a fresh spawn."""

    async def scenario():
        bulk._cleanup_task = None
        with patch.object(bulk, "_cleanup_old_jobs_locked", side_effect=RuntimeError("boom")):
            bulk._spawn_cleanup_if_idle()
            await bulk._cleanup_task  # completes without raising (exception logged)
        assert bulk._cleanup_task is None

        # Re-arm: a new spawn must create a new task, not be blocked by the old one.
        bulk._spawn_cleanup_if_idle()
        assert bulk._cleanup_task is not None
        await bulk._cleanup_task
        assert bulk._cleanup_task is None

    asyncio.run(scenario())
