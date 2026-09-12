# tests/test_s31_session_record.py
"""S31-2 (issue #76): structured session close capture + the mining queue.

Before this, ``agent_sessions.notes`` was write-only and unstructured — nothing
read it back. Now a close can carry decisions/mistakes/discoveries/gotchas/
workflows, and ``mined_at`` marks pending work for the miner.
"""

import asyncio
import json
import re
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from daemon import miner
from daemon.models.sessions import (
    RECORD_FIELDS,
    SessionPatchRequest,
    SessionRecord,
    SessionRecordItem,
)


# ---------------------------------------------------------------------------
# schema
# ---------------------------------------------------------------------------

def test_agent_sessions_has_mining_columns():
    """The queue lives in agent_sessions: session_record + mined_at."""
    sql = Path("init_db.sql").read_text(encoding="utf-8")
    body = sql.split("CREATE TABLE IF NOT EXISTS agent_sessions", 1)[1].split(");", 1)[0]
    assert "session_record JSONB" in body
    assert "mined_at" in body
    assert "idx_agent_sessions_mining" in sql


# ---------------------------------------------------------------------------
# validation
# ---------------------------------------------------------------------------

def test_record_item_requires_content():
    with pytest.raises(ValidationError):
        SessionRecordItem(content="   ")
    with pytest.raises(ValidationError):
        SessionRecordItem(content="x" * 2001)


def test_record_item_accepts_entities():
    item = SessionRecordItem(content="pinned the version", entities=["vault-memory"])
    assert item.entities == ["vault-memory"]


def test_record_counts_items_across_buckets():
    record = SessionRecord(
        decisions=[{"content": "a"}, {"content": "b"}],
        gotchas=[{"content": "c"}],
    )
    assert record.total_items() == 3
    assert record.mistakes == []


def test_patch_status_matches_the_db_check_constraint():
    """Regression: the validator used to accept 'paused'/'error', which the
    agent_sessions CHECK constraint then rejected as a DB error."""
    assert SessionPatchRequest(status="closed").status == "closed"
    assert SessionPatchRequest(status="idle").status == "idle"
    for bad in ("paused", "error", "done"):
        with pytest.raises(ValidationError):
            SessionPatchRequest(status=bad)

    sql = Path("init_db.sql").read_text(encoding="utf-8")
    check = re.search(r"status\s+TEXT\s+NOT NULL DEFAULT 'active'\s*\n?\s*CHECK \(status IN \(([^)]+)\)\)", sql)
    assert check, "could not locate the agent_sessions status CHECK constraint"
    assert "'closed'" in check.group(1)


# ---------------------------------------------------------------------------
# round-trip: model -> JSON -> normalised record
# ---------------------------------------------------------------------------

def test_record_json_round_trip_survives_normalisation():
    record = SessionRecord(
        decisions=[{"content": "chose a journaled write path"}],
        gotchas=[{"content": "pytest caches the old module", "entities": ["pytest"]}],
    )
    raw = record.model_dump_json()

    normalized = miner.normalize_record(raw)
    assert normalized["decisions"] == [{"content": "chose a journaled write path", "entities": []}]
    assert normalized["gotchas"][0]["entities"] == ["pytest"]
    assert miner.record_item_count(normalized) == 2


def test_normalize_record_handles_every_input_shape():
    # Postgres jsonb comes back as a dict.
    assert miner.normalize_record({"decisions": [{"content": "x"}]})["decisions"][0]["content"] == "x"
    # lite-mode SQLite returns TEXT.
    assert miner.normalize_record('{"decisions": [{"content": "y"}]}')["decisions"][0]["content"] == "y"
    # Bare strings are accepted (agents do this).
    assert miner.normalize_record({"gotchas": ["works on my machine"]})["gotchas"][0]["content"] == "works on my machine"
    # Everything else degrades to empty rather than raising.
    for junk in (None, "", "not json", 42, [], {"decisions": None}):
        normalized = miner.normalize_record(junk)
        assert set(normalized) == set(RECORD_FIELDS)
        assert miner.record_item_count(normalized) == 0


# ---------------------------------------------------------------------------
# mining queue
# ---------------------------------------------------------------------------

def test_mining_queue_sql_targets_closed_and_unmined():
    sql = " ".join(miner.MINING_QUEUE_SQL.split())
    assert "status = 'closed' AND mined_at IS NULL" in sql


def _pg(fetchall=None, fetchone=None):
    cursor = MagicMock()
    cursor.fetchall.return_value = fetchall if fetchall is not None else []
    cursor.fetchone.return_value = fetchone
    pg = MagicMock()
    pg.cursor.return_value.__enter__.return_value = cursor
    return pg, cursor


def test_mining_queue_normalises_rows():
    rows = [
        {
            "id": "aaaaaaaa-0000-0000-0000-000000000001",
            "agent_name": "claude-code",
            "project": "vault-memory",
            "task": "wire the miner",
            "notes": "freeform",
            "session_record": '{"gotchas": [{"content": "watch the queue"}]}',
            "closed_at": None,
        }
    ]
    pg, _ = _pg(fetchall=rows)
    queue = miner.mining_queue(pg, limit=5)
    assert len(queue) == 1
    assert queue[0]["session_id"] == rows[0]["id"]
    assert queue[0]["record"]["gotchas"][0]["content"] == "watch the queue"
    # A session with no structured capture is still queued (notes-only mining).
    assert miner.record_item_count(miner.normalize_record(None)) == 0


def test_mining_queue_tolerates_tuple_rows_and_errors():
    tuple_row = ("bbbbbbbb-0000-0000-0000-000000000002", "cli", "proj", "task", None, None, None)
    pg, _ = _pg(fetchall=[tuple_row])
    assert miner.mining_queue(pg)[0]["session_id"] == tuple_row[0]

    broken = MagicMock()
    broken.cursor.side_effect = RuntimeError("db down")
    assert miner.mining_queue(broken) == []


# ---------------------------------------------------------------------------
# route: PATCH persists the record and re-arms the queue
# ---------------------------------------------------------------------------

def test_session_patch_persists_record_as_json_string():
    from daemon.routes.sessions import session_patch

    cursor = MagicMock()
    cursor.fetchone.return_value = {"id": "s1"}
    pg = MagicMock()
    pg.cursor.return_value.__enter__.return_value = cursor
    deps = SimpleNamespace(postgres=pg, settings=SimpleNamespace(lite_mode=False))

    req = SessionPatchRequest(
        status="closed",
        session_record=SessionRecord(gotchas=[{"content": "mind the gap"}]),
    )
    res = asyncio.run(session_patch("s1", req, deps=deps, _auth="ok"))

    sql, params = cursor.execute.call_args[0]
    assert "UPDATE agent_sessions" in sql
    stored = params[list(req.model_dump(exclude_none=True).keys()).index("session_record")]
    assert isinstance(stored, str)
    assert json.loads(stored)["gotchas"][0]["content"] == "mind the gap"
    assert res["session_record_items"] == 1
    assert res["mined_at"] is None


def test_reopening_a_session_clears_mined_at():
    """A reopened session must return to the mining queue."""
    from daemon.routes.sessions import session_patch

    cursor = MagicMock()
    cursor.fetchone.return_value = {"id": "s1"}
    pg = MagicMock()
    pg.cursor.return_value.__enter__.return_value = cursor
    deps = SimpleNamespace(postgres=pg, settings=SimpleNamespace(lite_mode=False))

    res = asyncio.run(
        session_patch("s1", SessionPatchRequest(status="active"), deps=deps, _auth="ok")
    )
    assert "mined_at" in res["fields"]
    sql, params = cursor.execute.call_args[0]
    assert "mined_at = %s" in sql
    assert None in params
