from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

LINT_PATH = Path(__file__).resolve().parents[1] / "daemon" / "lint.py"
_spec = spec_from_file_location("daemon_lint_for_tests", LINT_PATH)
lint_mod = module_from_spec(_spec)
assert _spec and _spec.loader
_spec.loader.exec_module(lint_mod)

CONTRADICTION_REL_TYPES = lint_mod.CONTRADICTION_REL_TYPES
LintReport = lint_mod.LintReport
_find_contradictions = lint_mod._find_contradictions
_find_missing_pages = lint_mod._find_missing_pages


def test_lint_report_summary_counts():
    report = LintReport(
        run_at="2026-04-08T00:00:00+00:00",
        stale_days=30,
        orphans=[{"x": 1}],
        contradictions=[{"x": 1}, {"x": 2}],
        stale_nodes=[],
        missing_pages=[{"x": 1}],
        unlinked_pages=[{"x": 1}, {"x": 2}, {"x": 3}],
    )
    assert report.summary["orphans"] == 1
    assert report.summary["contradictions"] == 2
    assert report.summary["missing_pages"] == 1
    assert report.summary["unlinked_pages"] == 3
    assert report.summary["total_issues"] == 7


def test_find_missing_pages_uses_filesystem_stems(tmp_path, monkeypatch):
    (tmp_path / "Knowledge").mkdir(parents=True, exist_ok=True)
    (tmp_path / "Knowledge" / "Alpha.md").write_text("# Alpha", encoding="utf-8")

    def fake_query_rows(pg, sql, params=()):
        return [
            {"entity_name": "Alpha", "node_type": "entity", "centrality": 0.5},
            {"entity_name": "Beta Node", "node_type": "entity", "centrality": 0.2},
        ]

    monkeypatch.setattr(lint_mod, "_query_rows", fake_query_rows)
    missing = _find_missing_pages(pg=None, vault_root=tmp_path)
    assert [row["entity_name"] for row in missing] == ["Beta Node"]


def test_find_contradictions_uses_curated_relation_filter(monkeypatch):
    captured = {}

    def fake_query_rows(pg, sql, params=()):
        captured["params"] = params
        return []

    monkeypatch.setattr(lint_mod, "_query_rows", fake_query_rows)
    _find_contradictions(pg=None)
    assert captured["params"][0] == list(CONTRADICTION_REL_TYPES)
