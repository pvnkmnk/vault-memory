# tests/test_s31_miner.py
"""S31-3 (issue #77): distil closed sessions into lesson drafts.

The LLM is an injected callable, so every test here runs offline. Locked down:

- prompts carry the structured capture, the files the session touched, the
  dedup corpus, and the human's past rejection reasons;
- model responses are parsed tolerantly and malformed ones degrade to nothing;
- drafts always land in ``_working/sessions`` with ``review: pending`` — the
  miner must never write straight into the wiki;
- a candidate matching an existing lesson corroborates it instead of writing a
  near-duplicate page;
- a failing session is reported failed and stays in the queue, and triple
  persistence can fail without re-queueing a session whose drafts landed.
"""

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from daemon import miner


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

SESSION = {
    "session_id": "aaaaaaaa-0000-0000-0000-000000000001",
    "agent_name": "claude-code",
    "project": "vault-memory",
    "task": "wire the miner",
    "notes": "Took longer than expected.",
    "closed_at": None,
    "record": miner.normalize_record(
        {"decisions": [{"content": "queue lives in agent_sessions"}]}
    ),
}


def _pg(fetchall=None, fetchone=None):
    cursor = MagicMock()
    cursor.fetchall.return_value = fetchall if fetchall is not None else []
    cursor.fetchone.return_value = fetchone
    pg = MagicMock()
    pg.cursor.return_value.__enter__.return_value = cursor
    return pg, cursor


def _deps(pg=None):
    return SimpleNamespace(
        postgres=pg or _pg()[0],
        settings=SimpleNamespace(lite_mode=False),
    )


def _llm(payload):
    """An injectable LLM that returns a fixed response."""
    text = payload if isinstance(payload, str) else json.dumps(payload)

    async def _call(prompt, model=None):
        return text

    return _call


def _lesson(title="Always drain the queue", **over):
    body = {
        "title": title,
        "kind": "lesson",
        "content": "Drain the queue before asserting on it.",
        "entities": ["agent_sessions"],
        "confidence": "high",
    }
    body.update(over)
    return body


# ---------------------------------------------------------------------------
# prompt assembly
# ---------------------------------------------------------------------------

def test_prompt_carries_capture_notes_and_files():
    prompt = miner.build_mining_prompt(
        SESSION,
        files=["daemon/miner.py", "init_db.sql"],
        existing_lessons=[{"slug": "old-lesson", "title": "An old lesson"}],
        rejection_reasons=["Too generic: was rejected by a human"],
    )
    assert "queue lives in agent_sessions" in prompt
    assert "Took longer than expected." in prompt
    assert "daemon/miner.py" in prompt
    # Dedup context and the human-feedback loop are both injected.
    assert "An old lesson" in prompt
    assert "Too generic" in prompt


def test_prompt_degrades_when_nothing_was_captured():
    prompt = miner.build_mining_prompt(
        {"session_id": "s", "project": None, "task": None, "record": miner.empty_record()}
    )
    assert "(none captured)" in prompt
    assert "(none attributed)" in prompt
    assert "(none yet)" in prompt


# ---------------------------------------------------------------------------
# response parsing
# ---------------------------------------------------------------------------

def test_parse_candidates_accepts_prose_wrapped_json():
    parsed = miner.parse_candidates(
        'Sure! Here you go:\n```json\n{"lessons": [{"title": "T", "content": "C"}],'
        ' "triples": [{"subject": "a", "predicate": "uses", "object": "b"}]}\n```'
    )
    assert parsed["lessons"][0]["title"] == "T"
    assert parsed["lessons"][0]["kind"] == "lesson"
    assert parsed["triples"][0]["predicate"] == "uses"


def test_parse_candidates_accepts_bare_list_and_drops_incomplete_items():
    parsed = miner.parse_candidates(
        json.dumps(
            [
                _lesson(),
                {"title": "", "content": "no title"},
                {"title": "no content", "content": "   "},
                "not a dict",
                _lesson("Mind the gap", kind="GOTCHA"),
            ]
        )
    )
    assert [x["title"] for x in parsed["lessons"]] == ["Always drain the queue", "Mind the gap"]
    assert parsed["lessons"][1]["kind"] == "gotcha"


def test_parse_candidates_never_raises():
    for junk in (None, "", "not json at all", "{}", "[1,2,3]", "42"):
        parsed = miner.parse_candidates(junk)
        assert parsed == {"lessons": [], "triples": []}


# ---------------------------------------------------------------------------
# vault-side corpus
# ---------------------------------------------------------------------------

def test_collect_existing_lessons_reads_promoted_and_pending(tmp_path):
    lessons = tmp_path / "lessons"
    drafts = tmp_path / "_working" / "sessions"
    lessons.mkdir(parents=True)
    drafts.mkdir(parents=True)

    (lessons / "promoted.md").write_text(
        "---\ntitle: Promoted\ntype: lesson\nreview: approved\ncorroboration: 3\n---\nbody\n",
        encoding="utf-8",
    )
    (drafts / "pending.md").write_text(
        "---\ntitle: Pending\ntype: lesson\nreview: pending\n---\nbody\n",
        encoding="utf-8",
    )
    (drafts / "rejected.md").write_text(
        "---\ntitle: Rejected\ntype: lesson\nreview: rejected\n---\nbody\n",
        encoding="utf-8",
    )

    found = {item["slug"]: item for item in miner.collect_existing_lessons(tmp_path)}
    assert set(found) == {"promoted", "pending"}
    assert found["promoted"]["corroboration"] == 3
    assert found["pending"]["review"] == "pending"


def test_collect_rejection_reasons_is_scoped_to_project(tmp_path):
    rejected = tmp_path / "_working" / "sessions" / "rejected"
    rejected.mkdir(parents=True)
    (rejected / "a.md").write_text(
        "---\ntitle: Too vague\nproject: vault-memory\nrejection_reason: no evidence\n---\n",
        encoding="utf-8",
    )
    (rejected / "b.md").write_text(
        "---\ntitle: Other project\nproject: something-else\nrejection_reason: nope\n---\n",
        encoding="utf-8",
    )

    reasons = miner.collect_rejection_reasons(tmp_path, project="vault-memory")
    assert reasons == ["Too vague: no evidence"]


# ---------------------------------------------------------------------------
# drafts
# ---------------------------------------------------------------------------

def test_write_draft_is_always_pending_and_project_scoped(tmp_path):
    path = miner.write_draft(tmp_path, SESSION, _lesson())
    assert path.parent == tmp_path / "_working" / "sessions"

    text = path.read_text(encoding="utf-8")
    fm = miner.read_frontmatter(text)
    assert fm["review"] == "pending"
    assert fm["project"] == "vault-memory"
    assert fm["type"] == "lesson"
    assert fm["source"] == "session-mining"
    assert fm["corroboration"] == "1"
    assert SESSION["session_id"] in fm["sessions"]
    # Entities become wikilinks so the graph picks the draft up.
    assert "[[agent_sessions]]" in text


def test_write_draft_records_corroborating_sessions(tmp_path):
    path = miner.write_draft(
        tmp_path,
        SESSION,
        _lesson(),
        corroboration=2,
        session_ids=["s1", "s2"],
    )
    fm = miner.read_frontmatter(path.read_text(encoding="utf-8"))
    assert fm["corroboration"] == "2"
    assert "s1" in fm["sessions"] and "s2" in fm["sessions"]


def test_match_existing_uses_the_vault_slug_of_the_title():
    """Dedup keys off the same slug ``/promote`` uses for filenames."""
    slug = miner._slug_for("Always drain the queue!")
    existing = [{"slug": slug, "title": "Always drain the queue"}]
    assert miner.match_existing({"title": "Always drain the queue"}, existing)
    assert miner.match_existing({"title": "Something new"}, existing) is None


# ---------------------------------------------------------------------------
# orchestration
# ---------------------------------------------------------------------------

def test_mine_session_writes_drafts_and_persists_triples(tmp_path, monkeypatch):
    monkeypatch.setattr(miner, "files_touched", lambda pg, sid, limit=50: ["daemon/miner.py"])
    persisted = []
    monkeypatch.setattr(
        miner, "_default_persist_triples", lambda triples, deps: persisted.append(triples) or {"relationships_written": len(triples)}
    )

    result = asyncio.run(
        miner.mine_session(
            SESSION,
            _deps(),
            tmp_path,
            llm=_llm(
                {
                    "lessons": [_lesson()],
                    "triples": [{"subject": "miner", "predicate": "uses", "object": "agent_sessions"}],
                }
            ),
        )
    )

    assert result["status"] == "mined"
    assert len(result["drafts"]) == 1
    draft = result["drafts"][0]
    assert draft.startswith("_working/sessions/")
    assert draft.endswith("-" + miner._slug_for("Always drain the queue") + ".md")
    assert result["triples"] == 1
    assert result["files_considered"] == 1
    assert persisted[0][0]["predicate"] == "uses"
    assert (tmp_path / draft).exists()


def test_mine_session_corroborates_instead_of_duplicating(tmp_path, monkeypatch):
    monkeypatch.setattr(miner, "files_touched", lambda *a, **k: [])
    slug = miner._slug_for("Always drain the queue")
    lessons = tmp_path / "lessons"
    lessons.mkdir(parents=True)
    (lessons / (slug + ".md")).write_text(
        "---\ntitle: Always drain the queue\nproject: vault-memory\n---\nbody\n",
        encoding="utf-8",
    )

    result = asyncio.run(
        miner.mine_session(SESSION, _deps(), tmp_path, llm=_llm({"lessons": [_lesson()]}))
    )

    assert result["drafts"] == []
    assert result["corroborated"] == [slug]
    assert not (tmp_path / "_working" / "sessions" / (slug + ".md")).exists()


def test_triple_failure_does_not_re_queue_a_session(tmp_path, monkeypatch):
    """The graph write is best-effort; the drafts already landed."""
    monkeypatch.setattr(miner, "files_touched", lambda *a, **k: [])

    def _boom(triples, deps):
        raise RuntimeError("graph exploded")

    monkeypatch.setattr(miner, "_default_persist_triples", _boom)

    result = asyncio.run(
        miner.mine_session(
            SESSION,
            _deps(),
            tmp_path,
            llm=_llm({"lessons": [_lesson()], "triples": [{"subject": "a", "predicate": "b", "object": "c"}]}),
        )
    )
    assert result["status"] == "mined"
    assert result["relationships_written"] == 0


def test_mine_session_reports_failure_without_raising(tmp_path, monkeypatch):
    async def _explode(prompt, model=None):
        raise RuntimeError("ollama is down")

    result = asyncio.run(
        miner.mine_session(SESSION, _deps(), tmp_path, llm=_explode)
    )
    assert result["status"] == "failed"
    assert "ollama is down" in result["error"]


def test_mine_once_drains_the_queue_and_stamps_sessions(tmp_path, monkeypatch):
    pg, cursor = _pg()
    monkeypatch.setattr(miner, "mining_queue", lambda postgres, limit=20: [dict(SESSION)])
    monkeypatch.setattr(miner, "files_touched", lambda *a, **k: [])

    summary = asyncio.run(
        miner.mine_once(
            _deps(pg),
            tmp_path,
            llm=_llm({"lessons": [_lesson()]}),
        )
    )

    assert summary["queued"] == 1
    assert summary["mined"] == 1
    assert summary["failed"] == 0
    assert summary["drafts"] == 1

    statements = [c[0][0] for c in cursor.execute.call_args_list]
    assert any("mined_at = now()" in s for s in statements)


def test_mark_mined_records_the_error_instead_of_the_users_notes():
    pg, cursor = _pg()
    assert miner.mark_mined(pg, "s1", error="llm down") is True
    sql, params = cursor.execute.call_args[0]
    assert "mining_error = %s" in sql
    assert params == ("llm down", "s1")
    assert "notes" not in sql


def test_mark_mined_is_best_effort():
    broken = MagicMock()
    broken.cursor.side_effect = RuntimeError("db down")
    assert miner.mark_mined(broken, "s1") is False


def test_mining_is_off_unless_explicitly_enabled(monkeypatch):
    monkeypatch.delenv("SESSION_MINING", raising=False)
    assert miner.mining_enabled() is False
    monkeypatch.setenv("SESSION_MINING", "on")
    assert miner.mining_enabled() is True
    monkeypatch.setenv("SESSION_MINING", "OFF")
    assert miner.mining_enabled() is False


# ---------------------------------------------------------------------------
# entry points
# ---------------------------------------------------------------------------

def test_mine_route_refuses_in_lite_mode():
    from daemon.routes.sessions import sessions_mine

    deps = SimpleNamespace(settings=SimpleNamespace(lite_mode=True))
    res = asyncio.run(sessions_mine(deps=deps, _auth="ok"))
    # helpers.responses returns a JSONResponse for errors.
    assert res.status_code == 400


def test_mine_route_runs_the_miner(monkeypatch):
    from daemon.routes import sessions as sessions_route

    calls = {}

    async def _fake_mine_once(deps, vault_root, limit=5, llm=None):
        calls["limit"] = limit
        return {"queued": 0, "mined": 0, "failed": 0, "drafts": 0, "results": []}

    monkeypatch.setattr(miner, "mine_once", _fake_mine_once)
    monkeypatch.setattr(sessions_route, "_canonicalize_vault_root", lambda p: Path(p))

    deps = SimpleNamespace(
        settings=SimpleNamespace(lite_mode=False, vault_path="/tmp/vault")
    )
    res = asyncio.run(sessions_mine_call(deps, limit=99))
    assert calls["limit"] == 50  # clamped to the documented ceiling


def sessions_mine_call(deps, limit):
    from daemon.routes.sessions import sessions_mine

    return sessions_mine(deps=deps, _auth="ok", limit=limit)


def test_cli_exposes_the_mine_command():
    from cli.main import cli

    assert "mine" in cli.commands["sessions"].commands
