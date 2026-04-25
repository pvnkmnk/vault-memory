# DJI Open Issues Full Delivery Design

> For agentic workers: this design drives the implementation plan for completing DJI-205, DJI-202, DJI-201, DJI-200, and DJI-203 in order.

**Goal:** Close all remaining open sprint-board issues in sequence, delivering production-ready backend and plugin behavior with verification evidence.

**Architecture:** Use existing daemon and plugin foundations, patch missing behavior rather than refactor broadly, and add focused regression coverage. Sequence work by risk and dependency: backend batch/perf and security first, umbrella hygiene next, full plugin feature completion last.

**Tech Stack:** Python 3.11+, FastAPI, pytest, TypeScript, Obsidian plugin API, esbuild.

---

## Scope and Order

1. `DJI-205`: Implement/verify batch embedding + batch upsert sync path and remove duplicated `rel_path` calculation.
2. `DJI-202`: Keep as duplicate of `DJI-205`; verify no residual unique implementation work remains.
3. `DJI-201`: Validate and harden security controls (headers, rate limiting behavior, input validation paths) with tests/evidence.
4. `DJI-200`: Keep as umbrella/coordinator issue and refresh tracking notes for remaining/closed work.
5. `DJI-203`: Deliver full plugin feature set (settings/auth, search modes, ingest, graph rendering, auto-sync) and build verification.

## Design Decisions

- Prefer incremental edits in existing files (`daemon/sync_watcher.py`, `daemon/main.py`, `obsidian-plugin/src/*`) to preserve current behavior.
- Add narrowly scoped tests for regressions and acceptance criteria; avoid broad test rewrites.
- Keep plugin APIs configurable via settings instead of hardcoded daemon URL/API key.
- Keep issue lifecycle evidence in Linear comments with concrete commands/results.

## Verification Strategy

- Python: `python -m py_compile ...` and `python -m pytest tests/ -q --basetemp .pytest_tmp`.
- Plugin: `npm install` then `npm run build` in `obsidian-plugin`.
- For DJI-201/DJI-205, include targeted test commands and outcomes in Linear comments before closing.

## Risks and Mitigations

- Existing branch drift: mitigate by adding issue-specific regression tests before behavior changes.
- Plugin API mismatch with Obsidian runtime: mitigate with compile-time checks (`tsc`/esbuild) and API-safe patterns.
- Scope creep for DJI-203: enforce strict feature checklist and defer only non-required polish.
