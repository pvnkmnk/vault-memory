# tests/test_s32_digest.py
"""S32-2/3/4/5 (issues #82, #83, #84, #85): the digest ladder and skills export.

Offline throughout: Postgres is a stub that answers by SQL keyword, and the LLM
is injected. Locked down:

- daily/weekly/monthly each write their own file with the documented sections;
- the month's consolidation groups by theme, ignores one-off lessons, and drops
  proposals only into ``_working/consolidation/`` (never straight into skills/);
- ``skills export`` emits agentskills-compatible SKILL.md files whose referenced
  lesson pages actually exist, and never overwrites a hand-written SKILL.md;
- a missing model degrades to structure-only instead of failing the digest.
"""

import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from daemon import digest, lessons

NOW = datetime(2026, 9, 12, 0, 30, tzinfo=timezone.utc)  # a Saturday, hour 0
MONTH_START = datetime(2026, 9, 1, 0, 5, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# stubs
# ---------------------------------------------------------------------------

class _StubPostgres:
    """Answers the digest's queries by keyword, so no database is needed."""

    def __init__(self, *, pages=(), sessions=(), mined=0, actions=(), entities=()):
        self.pages = list(pages)
        self.sessions = list(sessions)
        self.mined = mined
        self.actions = list(actions)
        self.entities = list(entities)
        self.queries = []

    def cursor(self):
        outer = self

        class _Cursor:
            def __init__(self):
                self._rows = []

            def execute(self, sql, params=()):
                outer.queries.append(" ".join(sql.split()))
                compact = " ".join(sql.split())
                if "FROM sync_state" in compact:
                    self._rows = outer.pages
                elif "COUNT(*) FROM agent_sessions WHERE mined_at" in compact:
                    self._rows = [{"count": outer.mined}]
                elif "FROM agent_sessions" in compact:
                    self._rows = outer.sessions
                elif "GROUP BY action" in compact:
                    self._rows = outer.actions
                elif "GROUP BY source_name" in compact:
                    self._rows = outer.entities
                else:
                    self._rows = []

            def fetchall(self):
                return self._rows

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        return _Cursor()


def _deps(pg=None, vault_root=None):
    return SimpleNamespace(
        postgres=pg,
        settings=SimpleNamespace(lite_mode=False, vault_path=str(vault_root) if vault_root else None),
        watcher=None,
    )


def _lesson(
    vault_root: Path,
    slug: str,
    *,
    theme="build-loop",
    project="vault-memory",
    corroboration=2,
    review="approved",
    reviewed_at=NOW - timedelta(days=1),
    body="Do the thing.",
):
    directory = vault_root / ("lessons" if review != "pending" else "_working/sessions")
    directory.mkdir(parents=True, exist_ok=True)
    lines = [
        "---",
        f"title: {slug.replace('-', ' ').title()}",
        "type: lesson",
        f"project: {project}",
        f"theme: {theme}",
        f"review: {review}",
        "source: session-mining",
        f"corroboration: {corroboration}",
        "trust: high",
        "maturity: sapling",
        "decay-profile: log",
        f"reviewed_at: {reviewed_at.isoformat()}",
        "---",
        "",
        body,
    ]
    path = directory / (slug + ".md")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _llm(text="A short three-sentence summary."):
    async def _call(prompt, model=None):
        return text

    return _call


# ---------------------------------------------------------------------------
# period plumbing
# ---------------------------------------------------------------------------

def test_period_bounds_and_filenames():
    start, end = digest.period_bounds("daily", NOW)
    assert (end - start) == timedelta(days=1)
    assert digest.period_bounds("weekly", NOW)[0] == NOW - timedelta(days=7)
    assert digest.period_bounds("monthly", NOW)[0] == NOW - timedelta(days=30)
    with pytest.raises(ValueError):
        digest.period_bounds("hourly", NOW)

    assert digest.digest_filename("daily", NOW) == "2026-09-12.md"
    assert digest.digest_filename("monthly", NOW) == "2026-09.md"
    assert digest.digest_filename("weekly", NOW).startswith("2026-W")


def test_due_digest_only_fires_at_midnight(monkeypatch):
    assert digest.due_digest(NOW.replace(hour=14)) is None
    assert digest.due_digest(NOW) == "daily"
    assert digest.due_digest(NOW + timedelta(days=1)) == "weekly"  # Sunday
    assert digest.due_digest(MONTH_START) == "monthly"


def test_digests_are_off_unless_enabled(tmp_path, monkeypatch):
    monkeypatch.delenv("DIGESTS", raising=False)
    assert digest.digest_enabled() is False
    # Disabled: the heartbeat must not even touch the vault.
    assert asyncio.run(digest.run_due_digests(_deps(), tmp_path, now=NOW)) == []
    assert not (tmp_path / "digests").exists()

    monkeypatch.setenv("DIGESTS", "on")
    assert digest.digest_enabled() is True
    results = asyncio.run(digest.run_due_digests(_deps(), tmp_path, now=NOW))
    assert [r["kind"] for r in results] == ["daily"]


# ---------------------------------------------------------------------------
# daily (#82)
# ---------------------------------------------------------------------------

def test_daily_digest_writes_all_sections(tmp_path):
    pg = _StubPostgres(
        pages=[{"file_path": "05 Dev Projects/vault-memory/STATE.md", "maturity": "sapling"}],
        sessions=[
            {
                "id": "aaaaaaaa-1111",
                "agent_name": "claude-code",
                "project": "vault-memory",
                "task": "wire the digest",
            }
        ],
        mined=2,
        actions=[{"action": "modified", "count": 4}],
    )
    _lesson(tmp_path, "pending-one", review="pending")

    result = asyncio.run(
        digest.run_digest(_deps(pg, tmp_path), tmp_path, "daily", now=NOW, llm=_llm())
    )

    assert result["status"] == "written"
    assert result["path"] == "digests/2026-09-12.md"
    text = (tmp_path / result["path"]).read_text(encoding="utf-8")

    for section in (
        "## Changed pages (24h)",
        "## Sessions & lessons",
        "## Needs attention",
        "## Activity",
        "## Summary",
    ):
        assert section in text, section
    assert "decay-profile: log" in text
    assert "period: daily" in text
    assert "[[STATE]]" in text
    assert "Sessions closed: 1" in text
    assert "Sessions mined: 2" in text
    assert "modified: 4" in text
    assert "pending-one" in text


def test_daily_digest_survives_a_missing_model(tmp_path):
    result = asyncio.run(
        digest.run_digest(_deps(_StubPostgres(), tmp_path), tmp_path, "daily", now=NOW)
    )
    assert result["status"] == "written"
    assert result["summarised"] is False
    assert "## Summary" not in (tmp_path / result["path"]).read_text(encoding="utf-8")


def test_daily_digest_writes_even_when_the_model_fails(tmp_path):
    async def _boom(prompt, model=None):
        raise RuntimeError("ollama is down")

    result = asyncio.run(
        digest.run_digest(_deps(_StubPostgres(), tmp_path), tmp_path, "daily", now=NOW, llm=_boom)
    )
    assert result["status"] == "written"
    assert result["summarised"] is False


def test_daily_digest_flags_lint_and_the_inbox(tmp_path):
    from daemon import ingest

    ingest.ensure_inbox(tmp_path)
    (tmp_path / "inbox" / "queued.md").write_text("# Queued\n\nbody\n", encoding="utf-8")

    result = asyncio.run(
        digest.run_digest(_deps(_StubPostgres(), tmp_path), tmp_path, "daily", now=NOW, llm=_llm())
    )
    text = (tmp_path / result["path"]).read_text(encoding="utf-8")
    assert "waiting in the inbox" in text


def test_daily_summary_is_inserted_under_the_title_not_into_frontmatter(tmp_path):
    result = asyncio.run(
        digest.run_digest(
            _deps(_StubPostgres(), tmp_path), tmp_path, "daily", now=NOW, llm=_llm("Only the facts.")
        )
    )
    text = (tmp_path / result["path"]).read_text(encoding="utf-8")
    assert text.count("---") >= 2
    frontmatter = text.split("---")[1]
    assert "Only the facts." not in frontmatter
    assert text.index("# Daily digest") < text.index("## Summary")
    assert text.index("## Summary") < text.index("## Changed pages")


# ---------------------------------------------------------------------------
# weekly (#83)
# ---------------------------------------------------------------------------

def test_weekly_digest_sections_and_referenced_pages_exist(tmp_path):
    pg = _StubPostgres(
        pages=[
            {"file_path": "05 Dev Projects/vault-memory/STATE.md"},
            {"file_path": "05 Dev Projects/other-project/STATE.md"},
        ],
        sessions=[{"id": "s1", "agent_name": "cli", "project": "vault-memory", "task": "t"}],
        mined=3,
        entities=[{"entity": "vault-memory", "new_edges": 7}, {"entity": "obsidian", "new_edges": 2}],
    )
    promoted = _lesson(tmp_path, "corroborated-lesson", theme="testing", corroboration=3)
    _lesson(tmp_path, "one-off", review="pending")
    rejected = _lesson(tmp_path, "rejected-lesson", review="rejected")
    rejected.write_text(
        rejected.read_text(encoding="utf-8").replace("review: rejected", "review: rejected\nrejection_reason: too vague"),
        encoding="utf-8",
    )

    result = asyncio.run(
        digest.run_digest(_deps(pg, tmp_path), tmp_path, "weekly", now=NOW, llm=_llm())
    )
    text = (tmp_path / result["path"]).read_text(encoding="utf-8")

    for section in (
        "## Page velocity by project",
        "## Sessions mined",
        "## Lessons",
        "## Emerging entities",
        "## Contradictions flagged by lint",
        "## Sources ingested",
        "## Inbox queue",
    ):
        assert section in text, section

    assert "vault-memory: 1 pages" in text
    assert "other-project: 1 pages" in text
    assert "[[obsidian]]" in text
    assert "Rejection reasons fed back into mining: 1" in text
    assert "too vague" in text
    assert "period: weekly" in text
    assert "maturity: sapling" in text

    # Every lesson the digest links to is a page that exists on disk.
    assert promoted.exists()
    assert "[[corroborated-lesson]]" in text


def test_weekly_digest_lists_ingested_sources(tmp_path):
    from daemon import ingest

    source = ingest.Source(kind="url", ref="https://example.com/a", title="A Source", content="body")
    archived = ingest.archive_source(tmp_path, source)
    ingest.write_manifest(
        tmp_path,
        {
            "sources": {
                source.ref: {
                    "content_hash": source.content_hash,
                    "raw_path": archived["path"],
                    "archived_at": (NOW - timedelta(days=2)).isoformat(),
                    "compiled_at": (NOW - timedelta(days=2)).isoformat(),
                    "pages": ["Knowledge/A-Source.md"],
                }
            }
        },
    )

    result = asyncio.run(
        digest.run_digest(_deps(_StubPostgres(), tmp_path), tmp_path, "weekly", now=NOW, llm=_llm())
    )
    text = (tmp_path / result["path"]).read_text(encoding="utf-8")
    assert "https://example.com/a" in text
    assert "[[A-Source]]" in text
    assert result["ingested_sources"] == 1


# ---------------------------------------------------------------------------
# monthly consolidation (#84)
# ---------------------------------------------------------------------------

def test_monthly_review_groups_corroborated_lessons_by_theme(tmp_path):
    _lesson(tmp_path, "build-the-loop", theme="build-loop", corroboration=3)
    _lesson(tmp_path, "run-the-tests", theme="build-loop", corroboration=2)
    _lesson(tmp_path, "pin-the-schema", theme="graph-schema", corroboration=4)
    # A one-off must not become a skill: one session's take is not a way of working.
    _lesson(tmp_path, "lucky-guess", theme="build-loop", corroboration=1)

    result = asyncio.run(
        digest.run_digest(_deps(_StubPostgres(), tmp_path), tmp_path, "monthly", now=NOW)
    )

    assert result["status"] == "written"
    assert result["path"] == "digests/2026-09.md"
    assert result["themes"] == ["build-loop", "graph-schema"]

    for theme, expected in (
        ("build-loop", {"build-the-loop", "run-the-tests"}),
        ("graph-schema", {"pin-the-schema"}),
    ):
        skill = tmp_path / "_working" / "consolidation" / theme / "SKILL.md"
        assert skill.exists(), theme
        text = skill.read_text(encoding="utf-8")
        for slug in expected:
            assert f"[[{slug}]]" in text, (theme, slug)
        assert "lucky-guess" not in text

    # Proposals stay proposals: nothing lands in skills/.
    assert not (tmp_path / "skills").exists()
    review = (tmp_path / result["path"]).read_text(encoding="utf-8")
    assert "## Theme clusters" in review
    assert "## Proposed skill bundles" in review
    assert "_working/consolidation/build-loop/SKILL.md" in review


def test_monthly_review_records_rejection_themes(tmp_path):
    rejected = _lesson(tmp_path, "vague-one", review="rejected")
    rejected.write_text(
        rejected.read_text(encoding="utf-8").replace(
            "review: rejected", "review: rejected\nrejection_reason: too vague: no evidence"
        ),
        encoding="utf-8",
    )
    result = asyncio.run(
        digest.run_digest(_deps(_StubPostgres(), tmp_path), tmp_path, "monthly", now=NOW)
    )
    text = (tmp_path / result["path"]).read_text(encoding="utf-8")
    assert "### Rejection themes (fed back into mining prompts)" in text
    assert "too vague" in text


def test_cluster_by_theme_uses_the_lesson_theme_then_project():
    clusters = digest.cluster_by_theme(
        [
            {"slug": "a", "theme": "testing", "corroboration": 2},
            {"slug": "b", "theme": "", "project": "vault-memory", "corroboration": 3},
            {"slug": "c", "corroboration": 5},
        ]
    )
    assert set(clusters) == {"testing", "vault-memory", "general"}
    assert [m["slug"] for m in clusters["testing"]] == ["a"]


# ---------------------------------------------------------------------------
# skills export (#85)
# ---------------------------------------------------------------------------

def test_skills_export_writes_valid_bundles_with_existing_pages(tmp_path):
    """Acceptance: promote 2 lessons → export → SKILL.md frontmatter + pages."""
    for slug in ("build-the-loop", "run-the-tests"):
        draft = _lesson(tmp_path, slug, theme="build-loop", review="pending")
        assert lessons.promote_draft(tmp_path, str(draft))["ok"] is True
    _lesson(tmp_path, "pin-the-schema", theme="graph-schema", corroboration=2)

    result = digest.export_skills(tmp_path)

    assert result["themes"] == 2
    assert result["missing_pages"] == []
    written = {item["path"] for item in result["written"]}
    assert written == {"skills/build-loop/SKILL.md", "skills/graph-schema/SKILL.md"}

    bundle = (tmp_path / "skills" / "build-loop" / "SKILL.md").read_text(encoding="utf-8")
    # agentskills-compatible frontmatter.
    assert bundle.startswith("---\n")
    frontmatter = bundle.split("---")[1]
    assert "name: build-loop" in frontmatter
    assert "description: " in frontmatter
    assert f"generated-by: {digest.GENERATED_BY}" in frontmatter
    assert "## When to use" in bundle

    # Every lesson referenced by the bundle is a page that exists.
    for name in ("build-the-loop", "run-the-tests"):
        assert f"[[{name}]]" in bundle
        assert (tmp_path / "lessons" / f"{name}.md").exists()


def test_skills_export_never_overwrites_a_hand_written_bundle(tmp_path):
    _lesson(tmp_path, "build-the-loop", theme="build-loop", corroboration=2)
    manual = tmp_path / "skills" / "build-loop" / "SKILL.md"
    manual.parent.mkdir(parents=True)
    manual.write_text("---\nname: build-loop\n---\nHand written, do not clobber.\n", encoding="utf-8")

    result = digest.export_skills(tmp_path)

    assert result["written"] == []
    assert result["staged"][0]["path"] == "_working/skills/build-loop/SKILL.md"
    assert result["staged"][0]["reason"] == "existing SKILL.md was not generated here"
    assert "Hand written, do not clobber." in manual.read_text(encoding="utf-8")
    assert "[[build-the-loop]]" in (tmp_path / result["staged"][0]["path"]).read_text(encoding="utf-8")


def test_skills_export_regenerates_its_own_output(tmp_path):
    _lesson(tmp_path, "build-the-loop", theme="build-loop", corroboration=2)
    first = digest.export_skills(tmp_path)
    assert len(first["written"]) == 1

    second = digest.export_skills(tmp_path)
    assert second["staged"] == []
    assert len(second["written"]) == 1


def test_skills_export_reports_referenced_pages_that_are_missing(tmp_path):
    result = digest.export_skills(
        tmp_path,
        lesson_entries=[
            {
                "slug": "ghost",
                "title": "Ghost lesson",
                "theme": "build-loop",
                "path": "lessons/ghost.md",
                "corroboration": 2,
                "content": "x",
            }
        ],
    )
    assert result["missing_pages"] == ["lessons/ghost.md"]


def test_skills_export_respects_min_corroboration(tmp_path):
    _lesson(tmp_path, "strong", theme="testing", corroboration=3)
    _lesson(tmp_path, "weak", theme="testing", corroboration=1)

    assert digest.export_skills(tmp_path, min_corroboration=2)["themes"] == 1
    bundle = (tmp_path / "skills" / "testing" / "SKILL.md").read_text(encoding="utf-8")
    assert "[[strong]]" in bundle
    assert "[[weak]]" not in bundle


# ---------------------------------------------------------------------------
# routes
# ---------------------------------------------------------------------------

def test_digest_route_rejects_unknown_kinds_and_writes_known_ones(tmp_path):
    from daemon.routes.digest import run_digest_route

    deps = SimpleNamespace(
        postgres=None,
        settings=SimpleNamespace(lite_mode=False, vault_path=str(tmp_path)),
        watcher=None,
    )

    bad = asyncio.run(run_digest_route("hourly", None, deps=deps, _auth="ok"))
    assert bad.status_code == 400

    from daemon.models.digest import DigestRequest

    ok = asyncio.run(run_digest_route("daily", DigestRequest(summarise=False), deps=deps, _auth="ok"))
    assert ok["path"] == "digests/2026-09-12.md" or ok["path"].startswith("digests/")


def test_digest_and_skills_routes_list_what_was_written(tmp_path):
    from daemon.models.digest import SkillsExportRequest
    from daemon.routes.digest import list_digests, list_skills, skills_export

    _lesson(tmp_path, "build-the-loop", theme="build-loop", corroboration=2)
    deps = SimpleNamespace(
        postgres=None,
        settings=SimpleNamespace(lite_mode=False, vault_path=str(tmp_path)),
        watcher=None,
    )

    asyncio.run(digest.run_digest(deps, tmp_path, "daily", now=NOW))
    listed = asyncio.run(list_digests(deps=deps, _auth="ok"))
    assert listed["count"] == 1 and listed["digests"][0].endswith(".md")

    exported = asyncio.run(skills_export(SkillsExportRequest(), deps=deps, _auth="ok"))
    assert len(exported["written"]) == 1

    skills = asyncio.run(list_skills(deps=deps, _auth="ok"))
    assert skills["count"] == 1
    assert skills["skills"][0]["name"] == "build-loop"
    assert skills["skills"][0]["generated"] is True
    assert skills["skills"][0]["description"]


def test_skills_export_request_validates(tmp_path):
    from pydantic import ValidationError

    from daemon.models.digest import SkillsExportRequest

    assert SkillsExportRequest().min_corroboration == 1
    for bad in (0, 21):
        with pytest.raises(ValidationError):
            SkillsExportRequest(min_corroboration=bad)


def test_heartbeat_runs_digests_only_when_enabled(tmp_path, monkeypatch):
    from daemon.heartbeat import HeartbeatJob

    job = HeartbeatJob(MagicMock(), 900, vault_root=tmp_path)
    monkeypatch.delenv("DIGESTS", raising=False)
    monkeypatch.delenv("SESSION_MINING", raising=False)

    asyncio.run(job._heartbeat_cycle())
    assert not (tmp_path / "digests").exists()
