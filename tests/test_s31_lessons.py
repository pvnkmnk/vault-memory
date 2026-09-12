# tests/test_s31_lessons.py
"""S31-4 (issue #78): the lesson review gate.

Mined drafts are trust-sensitive, so nothing reaches ``lessons/`` without a
decision. What this locks down:

- promote moves a pending draft into ``lessons/`` and marks it approved;
- reject *stores the reason*, and that reason really does show up in the next
  mining prompt for the project (the feedback loop);
- a candidate matching an existing lesson corroborates it instead of writing a
  near-duplicate;
- auto-promotion is off unless asked for, and ``conservative`` needs evidence.
"""

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from daemon import lessons, miner


DRAFT = """---
title: Pin the provider version
type: lesson
project: vault-memory
review: pending
source: session-mining
sessions: [s1]
corroboration: 1
agent-confidence: high
trust: low
maturity: seed
---

Always pin the provider version before the first fetch.
[[fastapi]]
"""


def _seed_draft(tmp_path: Path, name="2026-09-12-pin-the-provider-version.md", text=DRAFT) -> Path:
    drafts = tmp_path / "_working" / "sessions"
    drafts.mkdir(parents=True, exist_ok=True)
    path = drafts / name
    path.write_text(text, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# frontmatter round-trip
# ---------------------------------------------------------------------------

def test_frontmatter_round_trips_through_render():
    frontmatter, body = lessons.split_frontmatter(DRAFT)
    assert frontmatter["review"] == "pending"
    assert body.startswith("Always pin the provider version")

    rendered = lessons.render_frontmatter(frontmatter)
    assert lessons.parse_frontmatter(rendered + "\nbody\n") == frontmatter


def test_rejection_reason_with_punctuation_survives_a_round_trip():
    fm = {"title": "T", "rejection_reason": 'Too vague: "no evidence" #1', "review": "rejected"}
    text = lessons.render_frontmatter(fm) + "\nbody\n"
    assert lessons.parse_frontmatter(text)["rejection_reason"] == 'Too vague: "no evidence" #1'


def test_parse_list_handles_lists_scalars_and_blanks():
    assert lessons.parse_list("[a, b]") == ["a", "b"]
    assert lessons.parse_list("a, b") == ["a", "b"]
    assert lessons.parse_list("[]") == []
    assert lessons.parse_list(None) == []
    assert lessons.parse_list(["x"]) == ["x"]


# ---------------------------------------------------------------------------
# promote
# ---------------------------------------------------------------------------

def test_promote_moves_the_draft_into_lessons(tmp_path):
    path = _seed_draft(tmp_path)

    result = lessons.promote_draft(tmp_path, str(path), reviewer="human")

    assert result["ok"] is True
    # The miner's YYYY-MM-DD- prefix is dropped for the permanent page.
    assert result["path"] == "lessons/pin-the-provider-version.md"

    promoted = lessons.parse_draft(tmp_path / result["path"], tmp_path)
    assert promoted.review == "approved"
    assert promoted.frontmatter["maturity"] == "sapling"
    assert promoted.frontmatter["reviewer"] == "human"
    assert "reviewed_at" in promoted.frontmatter
    # Uncorroborated process knowledge stays medium-trust.
    assert promoted.frontmatter["trust"] == "medium"
    # Body (and its wikilinks) are preserved verbatim.
    assert "[[fastapi]]" in promoted.body
    # The draft is consumed: otherwise the miner would re-corroborate a lesson
    # that is already in the wiki.
    assert not path.exists()


def test_promote_is_idempotent_on_an_already_approved_lesson(tmp_path):
    path = _seed_draft(tmp_path)
    lessons.promote_draft(tmp_path, str(path))
    again = lessons.promote_draft(tmp_path, "pin-the-provider-version")
    assert again["ok"] is True
    assert again["already_promoted"] is True


def test_promote_unknown_draft_reports_instead_of_raising(tmp_path):
    result = lessons.promote_draft(tmp_path, "does-not-exist")
    assert result["ok"] is False
    assert "not found" in result["error"]


def test_high_corroboration_lesson_promotes_as_high_trust(tmp_path):
    text = DRAFT.replace("corroboration: 1", "corroboration: 3")
    path = _seed_draft(tmp_path, text=text)
    lessons.promote_draft(tmp_path, str(path))
    promoted = lessons.parse_draft(tmp_path / "lessons" / "pin-the-provider-version.md", tmp_path)
    assert promoted.frontmatter["trust"] == "high"


# ---------------------------------------------------------------------------
# reject + the feedback loop
# ---------------------------------------------------------------------------

def test_reject_requires_a_reason(tmp_path):
    path = _seed_draft(tmp_path)
    result = lessons.reject_draft(tmp_path, str(path), "   ")
    assert result["ok"] is False
    assert path.exists()


def test_reject_stores_the_reason_and_archives_the_draft(tmp_path):
    path = _seed_draft(tmp_path)

    result = lessons.reject_draft(
        tmp_path, str(path), "Too generic — no evidence from the session", reviewer="human"
    )

    assert result["ok"] is True
    assert result["path"] == "_working/sessions/rejected/2026-09-12-pin-the-provider-version.md"
    assert not path.exists()

    rejected = lessons.parse_draft(tmp_path / result["path"], tmp_path)
    assert rejected.review == "rejected"
    assert rejected.frontmatter["rejection_reason"].startswith("Too generic")
    assert "rejected_at" in rejected.frontmatter


def test_rejection_reason_reaches_the_next_mining_prompt(tmp_path):
    """The acceptance criterion: reject → the reason is injected into mining."""
    path = _seed_draft(tmp_path)
    lessons.reject_draft(tmp_path, str(path), "Too generic — no evidence from the session")

    reasons = miner.collect_rejection_reasons(tmp_path, project="vault-memory")
    assert reasons == ["Pin the provider version: Too generic — no evidence from the session"]

    prompt = miner.build_mining_prompt(
        {
            "session_id": "s2",
            "project": "vault-memory",
            "task": "next task",
            "record": miner.empty_record(),
        },
        rejection_reasons=reasons,
    )
    assert "Too generic — no evidence from the session" in prompt
    # And the rejected draft must not also count as an existing lesson to dedup against.
    assert "2026-09-12-pin-the-provider-version" not in {
        d.slug for d in lessons.list_drafts(tmp_path, review=None)
    }


# ---------------------------------------------------------------------------
# corroboration over duplication
# ---------------------------------------------------------------------------

def test_corroborate_bumps_the_count_and_links_the_session(tmp_path):
    path = _seed_draft(tmp_path)

    result = lessons.corroborate(tmp_path, str(path), "session-2")

    assert result["corroboration"] == 2
    assert result["sessions"] == ["s1", "session-2"]
    assert result["already_linked"] is False

    reread = lessons.parse_draft(path, tmp_path)
    assert reread.corroboration == 2
    assert reread.sessions == ["s1", "session-2"]
    # Corroboration must not change the review state.
    assert reread.review == "pending"


def test_corroborate_is_idempotent_for_the_same_session(tmp_path):
    path = _seed_draft(tmp_path)
    lessons.corroborate(tmp_path, str(path), "s1")
    again = lessons.corroborate(tmp_path, str(path), "s1")
    assert again["already_linked"] is True
    assert again["corroboration"] == 1


def test_corroborate_returns_none_when_there_is_nothing_to_merge(tmp_path):
    assert lessons.corroborate(tmp_path, "nope", "s1") is None


def test_mining_corroborates_instead_of_writing_a_duplicate(tmp_path, monkeypatch):
    """The end-to-end merge: a repeat lesson bumps the existing page."""
    promoted = tmp_path / "lessons"
    promoted.mkdir(parents=True)
    (promoted / "pin-the-provider-version.md").write_text(
        DRAFT.replace("review: pending", "review: approved"), encoding="utf-8"
    )

    monkeypatch.setattr(miner, "files_touched", lambda *a, **k: [])
    monkeypatch.setattr(miner, "_default_persist_triples", lambda t, d: {"relationships_written": 0})

    result = asyncio.run(
        miner.mine_session(
            {
                "session_id": "session-2",
                "project": "vault-memory",
                "task": "t",
                "record": miner.empty_record(),
                "closed_at": None,
            },
            SimpleNamespace(postgres=MagicMock(), settings=None),
            tmp_path,
            llm=_llm(
                {
                    "lessons": [
                        {
                            "title": "Pin the provider version",
                            "content": "Pin it.",
                            "entities": [],
                            "confidence": "high",
                        }
                    ]
                }
            ),
        )
    )

    assert result["corroborated"] == ["pin-the-provider-version"]
    assert result["drafts"] == []
    merged = lessons.parse_draft(promoted / "pin-the-provider-version.md", tmp_path)
    assert merged.corroboration == 2
    assert "session-2" in merged.sessions


def _llm(payload):
    import json

    text = json.dumps(payload)

    async def _call(prompt, model=None):
        return text

    return _call


# ---------------------------------------------------------------------------
# auto-promote policy
# ---------------------------------------------------------------------------

def test_policy_defaults_to_off(monkeypatch):
    monkeypatch.delenv("SESSION_MINING_AUTO_PROMOTE", raising=False)
    assert lessons.auto_promote_policy() == "off"
    monkeypatch.setenv("SESSION_MINING_AUTO_PROMOTE", "CONSERVATIVE")
    assert lessons.auto_promote_policy() == "conservative"
    monkeypatch.setenv("SESSION_MINING_AUTO_PROMOTE", "yolo")
    assert lessons.auto_promote_policy() == "off"


def test_conservative_needs_two_independent_sessions(tmp_path):
    _seed_draft(tmp_path)
    draft = lessons.list_drafts(tmp_path)[0]
    assert lessons.should_auto_promote(draft, "conservative") is False

    lessons.corroborate(tmp_path, "2026-09-12-pin-the-provider-version", "session-2")
    draft = lessons.list_drafts(tmp_path)[0]
    assert lessons.should_auto_promote(draft, "conservative") is True


def test_aggressive_promotes_every_pending_draft(tmp_path):
    _seed_draft(tmp_path)
    draft = lessons.list_drafts(tmp_path)[0]
    assert lessons.should_auto_promote(draft, "aggressive") is True


def test_apply_auto_promote_is_a_noop_when_off(tmp_path, monkeypatch):
    _seed_draft(tmp_path)
    monkeypatch.delenv("SESSION_MINING_AUTO_PROMOTE", raising=False)
    assert lessons.apply_auto_promote(tmp_path) == []
    assert lessons.list_drafts(tmp_path, review="approved") == []


def test_apply_auto_promote_aggressive_lands_the_lesson(tmp_path, monkeypatch):
    _seed_draft(tmp_path)
    monkeypatch.setenv("SESSION_MINING_AUTO_PROMOTE", "aggressive")
    promoted = lessons.apply_auto_promote(tmp_path)
    assert [p["path"] for p in promoted] == ["lessons/pin-the-provider-version.md"]
    assert (tmp_path / "lessons" / "pin-the-provider-version.md").exists()


# ---------------------------------------------------------------------------
# find_draft resolution
# ---------------------------------------------------------------------------

def test_find_draft_accepts_path_or_slug(tmp_path):
    path = _seed_draft(tmp_path)
    for name in (str(path), "_working/sessions/2026-09-12-pin-the-provider-version", "2026-09-12-pin-the-provider-version", "pin-the-provider-version"):
        found = lessons.find_draft(tmp_path, name)
        assert found is not None, name
        assert found.slug == "2026-09-12-pin-the-provider-version"


# ---------------------------------------------------------------------------
# lint rule
# ---------------------------------------------------------------------------

def test_lint_flags_a_mined_lesson_that_touches_a_contested_entity(tmp_path):
    """A low-trust mined lesson must not silently win against the graph."""
    from daemon.lint import _find_lesson_conflicts

    _seed_draft(tmp_path)
    contradictions = [
        {
            "source_name": "fastapi",
            "relationship_type": "status",
            "conflicting_targets": ["stable", "deprecated"],
        }
    ]

    flagged = _find_lesson_conflicts(MagicMock(), tmp_path, contradictions)

    assert len(flagged) == 1
    assert flagged[0]["vault_path"] == "_working/sessions/2026-09-12-pin-the-provider-version.md"
    assert flagged[0]["review"] == "pending"
    assert flagged[0]["conflicts"][0]["entity"] == "fastapi"
    assert flagged[0]["conflicts"][0]["conflicting_targets"] == ["stable", "deprecated"]


def test_lint_ignores_lessons_that_do_not_touch_a_contested_entity(tmp_path):
    from daemon.lint import _find_lesson_conflicts

    _seed_draft(tmp_path)
    assert _find_lesson_conflicts(MagicMock(), tmp_path, []) == []
    assert _find_lesson_conflicts(
        MagicMock(),
        tmp_path,
        [{"source_name": "django", "relationship_type": "status", "conflicting_targets": ["a", "b"]}],
    ) == []


def test_lint_ignores_hand_written_lessons(tmp_path):
    """Only mined lessons are flagged; a human wrote the rest."""
    from daemon.lint import _find_lesson_conflicts

    _seed_draft(tmp_path, text=DRAFT.replace("source: session-mining\n", ""))
    contradictions = [{"source_name": "fastapi", "relationship_type": "status", "conflicting_targets": ["a", "b"]}]
    assert _find_lesson_conflicts(MagicMock(), tmp_path, contradictions) == []


# ---------------------------------------------------------------------------
# routes
# ---------------------------------------------------------------------------

def _route_deps(tmp_path):
    return SimpleNamespace(
        postgres=MagicMock(),
        settings=SimpleNamespace(lite_mode=False, vault_path=str(tmp_path)),
        watcher=None,
    )


def test_review_route_lists_pending_drafts(tmp_path):
    from daemon.routes.lessons import list_lesson_review

    _seed_draft(tmp_path)
    res = asyncio.run(list_lesson_review(deps=_route_deps(tmp_path), _auth="ok"))

    assert res["count"] == 1
    assert res["auto_promote_policy"] == "off"
    assert res["drafts"][0]["title"] == "Pin the provider version"
    assert res["drafts"][0]["entities"] == ["fastapi"]


def test_promote_route_moves_the_draft(tmp_path):
    from daemon.models.lessons import LessonPromoteRequest
    from daemon.routes.lessons import promote_lesson

    _seed_draft(tmp_path)
    request = MagicMock()
    request.headers = {}

    res = asyncio.run(
        promote_lesson(
            LessonPromoteRequest(name="pin-the-provider-version"),
            request,
            deps=_route_deps(tmp_path),
            _auth="ok",
        )
    )
    assert res["ok"] is True
    assert (tmp_path / "lessons" / "pin-the-provider-version.md").exists()


def test_reject_route_requires_and_stores_a_reason(tmp_path):
    from pydantic import ValidationError

    from daemon.models.lessons import LessonRejectRequest

    with pytest.raises(ValidationError):
        LessonRejectRequest(name="x", reason="   ")

    _seed_draft(tmp_path)
    req = LessonRejectRequest(name="pin-the-provider-version", reason="no evidence")

    from daemon.routes.lessons import reject_lesson

    res = asyncio.run(reject_lesson(req, deps=_route_deps(tmp_path), _auth="ok"))
    assert res["ok"] is True
    assert (tmp_path / res["path"]).exists()


def test_new_mcp_tools_are_registered_and_dispatch_to_the_daemon(monkeypatch):
    """Advertised tools must actually resolve — an unknown tool raises."""
    import httpx

    import cli.mcp_adapter as adapter
    import cli.mcp_client as client

    names = {t["name"] for t in adapter.TOOLS}
    assert {"memory/lesson_review", "memory/lesson_promote", "memory/lesson_reject"} <= names

    calls = []

    class _Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"ok": True}

    def _get(url, **kwargs):
        calls.append(("GET", url, kwargs.get("params")))
        return _Response()

    def _post(url, **kwargs):
        calls.append(("POST", url, kwargs.get("json")))
        return _Response()

    monkeypatch.setattr(httpx, "get", _get)
    monkeypatch.setattr(httpx, "post", _post)

    client.call_daemon("http://d", "memory/lesson_review", {"review": "pending"})
    client.call_daemon("http://d", "memory/lesson_promote", {"name": "x"})
    client.call_daemon("http://d", "memory/lesson_reject", {"name": "x", "reason": "why"})

    assert calls[0][1] == "http://d/lessons/review"
    assert calls[1][1] == "http://d/lessons/promote"
    assert calls[2][2] == {"name": "x", "reason": "why"}

    with pytest.raises(ValueError):
        client.call_daemon("http://localhost:5051", "memory/definitely_not_a_tool", {})
