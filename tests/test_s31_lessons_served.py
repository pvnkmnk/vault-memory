# tests/test_s31_lessons_served.py
"""S31-5 (issue #79): serve lessons — project_state tells the next agent.

The payoff: a session that starts fresh still knows what previous sessions
learned. Locked down here:

- a promoted lesson for project X appears in the bundle for project X and not
  in another project's bundle;
- ranking really is recency x corroboration x trust (each factor can flip an
  ordering on its own);
- the lesson section respects a token budget without silently dropping lessons;
- a lesson page carries the ``log`` decay profile, and that profile is actually
  registered, so lessons do not decay at the 30-day "active" rate.
"""

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from daemon import lessons

NOW = datetime(2026, 9, 12, tzinfo=timezone.utc)


def _lesson(tmp_path: Path, slug: str, *, project="vault-memory", corroboration=1,
            trust="high", age_days=0, created="date_created", body="Do the thing."):
    directory = tmp_path / "lessons"
    directory.mkdir(parents=True, exist_ok=True)
    stamp = (NOW - timedelta(days=age_days)).isoformat()
    lines = [
        "---",
        f"title: {slug.replace('-', ' ').title()}",
        "type: lesson",
        f"project: {project}",
        "review: approved",
        "source: session-mining",
        f"corroboration: {corroboration}",
        f"trust: {trust}",
        "maturity: sapling",
        "decay-profile: log",
        f"{created}: {stamp}",
        "---",
        "",
        body,
    ]
    path = directory / (slug + ".md")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# score factors
# ---------------------------------------------------------------------------

def test_recency_score_decays_and_treats_undated_as_current():
    assert lessons.recency_score(None, now=NOW) == 1.0
    assert lessons.recency_score("not a date", now=NOW) == 1.0
    assert lessons.recency_score(NOW.isoformat(), now=NOW) == 1.0
    half_life = lessons.recency_score(
        (NOW - timedelta(days=lessons.LESSON_DECAY_DAYS)).isoformat(), now=NOW
    )
    assert half_life == 1 / 2.718281828459045  # one e-fold
    assert lessons.recency_score((NOW - timedelta(days=10_000)).isoformat(), now=NOW) < 0.01


def test_corroboration_score_is_monotonic_and_saturates():
    scores = [lessons.corroboration_score(n) for n in range(0, 8)]
    assert scores[0] == 0.0
    # Strictly increasing until it saturates, then flat.
    assert all(scores[n] < scores[n + 1] for n in range(1, lessons.LESSON_MAX_CORROBORATION))
    assert scores[lessons.LESSON_MAX_CORROBORATION] == 1.0
    assert scores[7] == 1.0


def test_trust_score_orders_review_outcomes_and_defaults_low():
    assert lessons.trust_score("high") > lessons.trust_score("medium") > lessons.trust_score("low")
    assert lessons.trust_score(None) == lessons.trust_score("low")
    assert lessons.trust_score("nonsense") == lessons.trust_score("low")


def test_lesson_score_is_the_product_of_the_three_factors(tmp_path):
    path = _lesson(tmp_path, "fresh-corroborated")
    draft = lessons.parse_draft(path, tmp_path)
    expected = (
        lessons.recency_score(draft.frontmatter["date_created"], now=NOW)
        * lessons.corroboration_score(draft.corroboration)
        * lessons.trust_score(draft.frontmatter["trust"])
    )
    assert lessons.lesson_score(draft, now=NOW) == expected


# ---------------------------------------------------------------------------
# ranking
# ---------------------------------------------------------------------------

def test_rank_lessons_scores_corroboration_over_recency(tmp_path):
    _lesson(tmp_path, "one-off-now", corroboration=1, age_days=0)
    _lesson(tmp_path, "confirmed-three-times", corroboration=3, age_days=45)

    ranked = lessons.rank_lessons(tmp_path, project="vault-memory", now=NOW)
    assert [item["slug"] for item in ranked] == ["confirmed-three-times", "one-off-now"]
    assert ranked[0]["score"] > ranked[1]["score"]


def test_rank_lessons_lets_recency_beat_stale_corroboration(tmp_path):
    _lesson(tmp_path, "fresh", corroboration=1, age_days=1)
    _lesson(tmp_path, "ancient", corroboration=4, age_days=3_650)

    ranked = lessons.rank_lessons(tmp_path, project="vault-memory", now=NOW)
    assert [item["slug"] for item in ranked] == ["fresh", "ancient"]


def test_rank_lessons_is_scoped_to_the_project(tmp_path):
    _lesson(tmp_path, "mine", project="vault-memory")
    _lesson(tmp_path, "theirs", project="other-project")

    slugs = [item["slug"] for item in lessons.rank_lessons(tmp_path, project="vault-memory")]
    assert slugs == ["mine"]
    assert [i["slug"] for i in lessons.rank_lessons(tmp_path)] == ["mine", "theirs"]


def test_rank_lessons_excludes_unreviewed_drafts(tmp_path):
    _lesson(tmp_path, "approved-one")
    drafts = tmp_path / "_working" / "sessions"
    drafts.mkdir(parents=True)
    (drafts / "still-pending.md").write_text(
        "---\ntitle: Still pending\nproject: vault-memory\nreview: pending\ncorroboration: 9\n---\nb\n",
        encoding="utf-8",
    )
    assert [i["slug"] for i in lessons.rank_lessons(tmp_path, project="vault-memory")] == ["approved-one"]


def test_rank_lessons_honours_top_k(tmp_path):
    for n in range(4):
        _lesson(tmp_path, f"lesson-{n}", corroboration=n + 1)
    assert len(lessons.rank_lessons(tmp_path, top_k=2)) == 2


def test_rank_lessons_respects_the_token_budget_without_dropping_lessons(tmp_path):
    _lesson(tmp_path, "long-first", corroboration=5, body="word " * 4000)
    _lesson(tmp_path, "short-second", corroboration=1, body="tiny")

    ranked = lessons.rank_lessons(tmp_path, token_budget=50)

    # Both lessons are still listed — the agent learns they exist.
    assert [i["slug"] for i in ranked] == ["long-first", "short-second"]
    assert ranked[0]["truncated"] is True
    assert ranked[0]["tokens"] <= 50
    assert sum(i["tokens"] for i in ranked) <= 50
    # An unaffordable lesson reports nothing rather than an unbounded body.
    assert ranked[1]["content"] in ("", "tiny")


def test_rank_lessons_reports_the_decay_profile_and_metadata(tmp_path):
    _lesson(tmp_path, "pin-the-version", corroboration=2)
    ranked = lessons.rank_lessons(tmp_path, project="vault-memory", now=NOW)
    assert ranked[0]["decay_profile"] == "log"
    assert ranked[0]["maturity"] == "sapling"
    assert ranked[0]["trust"] == "high"
    assert ranked[0]["corroboration"] == 2
    assert ranked[0]["path"] == "lessons/pin-the-version.md"


# ---------------------------------------------------------------------------
# decay profile wiring
# ---------------------------------------------------------------------------

def test_log_decay_profile_is_registered_and_slower_than_active():
    from daemon.retrieval import DECAY_PROFILES

    assert "log" in DECAY_PROFILES, "lessons would silently decay at the active rate"
    assert DECAY_PROFILES["log"] > DECAY_PROFILES["active"]


# ---------------------------------------------------------------------------
# route
# ---------------------------------------------------------------------------

def test_lessons_route_returns_ranked_lessons_for_the_project(tmp_path):
    from daemon.routes.lessons import list_lessons

    _lesson(tmp_path, "mine", project="vault-memory")
    _lesson(tmp_path, "theirs", project="other")

    deps = SimpleNamespace(settings=SimpleNamespace(vault_path=str(tmp_path), lite_mode=False))
    res = asyncio.run(list_lessons(deps=deps, _auth="ok", project="vault-memory", top_k=5))

    assert res["count"] == 1
    assert res["lessons"][0]["slug"] == "mine"
    assert res["token_budget"] == lessons.DEFAULT_LESSON_TOKENS
    assert res["tokens_used"] >= 0


def test_lessons_route_clamps_top_k(tmp_path):
    from daemon.routes.lessons import list_lessons

    for n in range(3):
        _lesson(tmp_path, f"l{n}")
    deps = SimpleNamespace(settings=SimpleNamespace(vault_path=str(tmp_path), lite_mode=False))
    res = asyncio.run(list_lessons(deps=deps, _auth="ok", top_k=0))
    assert res["count"] == 1  # clamped to the minimum of 1


# ---------------------------------------------------------------------------
# memory/project_state
# ---------------------------------------------------------------------------

class _Resp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


def _project_state_env(tmp_path, monkeypatch, lessons_payload, *, lessons_boom=False):
    import httpx

    from cli.tools import context as context_tools

    project_dir = tmp_path / "05 Dev Projects" / "vault-memory"
    project_dir.mkdir(parents=True)
    (project_dir / "vault-memory.md").write_text("identity", encoding="utf-8")

    seen = {}

    def _get(url, **kwargs):
        seen["get"] = (url, kwargs.get("params"))
        if lessons_boom:
            raise RuntimeError("daemon down")
        return _Resp(lessons_payload)

    monkeypatch.setattr(httpx, "get", _get)
    monkeypatch.setattr(httpx, "post", lambda url, **kw: _Resp({"results": []}))
    return context_tools, seen


def test_project_state_includes_promoted_lessons(tmp_path, monkeypatch):
    _lesson(tmp_path, "mine", project="vault-memory", corroboration=3)
    ranked = lessons.rank_lessons(tmp_path, project="vault-memory", now=NOW)

    context_tools, seen = _project_state_env(tmp_path, monkeypatch, {"lessons": ranked})
    res = context_tools._memory_project_state(
        {"project": "vault-memory", "vault_path": str(tmp_path)}, "http://d"
    )

    assert [l["slug"] for l in res["lessons"]] == ["mine"]
    assert res["lessons_error"] is None
    assert seen["get"][0] == "http://d/lessons"
    assert seen["get"][1]["project"] == "vault-memory"
    # The lesson text is not free — it must show up in the advertised cost.
    assert res["token_cost"] >= len("Do the thing.") // 4


def test_project_state_survives_a_lesson_fetch_failure(tmp_path, monkeypatch):
    context_tools, _ = _project_state_env(tmp_path, monkeypatch, {}, lessons_boom=True)
    res = context_tools._memory_project_state(
        {"project": "vault-memory", "vault_path": str(tmp_path)}, "http://d"
    )
    assert res["lessons"] == []
    assert res["lessons_error"]
    # Everything else still loads.
    assert res["project_identity"] == "identity"
    assert res["current_state"]


def test_project_state_lesson_args_reach_the_daemon(tmp_path, monkeypatch):
    context_tools, seen = _project_state_env(tmp_path, monkeypatch, {"lessons": []})
    context_tools._memory_project_state(
        {
            "project": "vault-memory",
            "vault_path": str(tmp_path),
            "lesson_top_k": 3,
            "lesson_token_budget": 120,
        },
        "http://d",
    )
    assert seen["get"][1] == {"project": "vault-memory", "top_k": 3, "token_budget": 120}
