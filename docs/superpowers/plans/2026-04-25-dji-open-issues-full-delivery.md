# DJI Open Issues Full Delivery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Complete open Linear issues DJI-205, DJI-202, DJI-201, DJI-200, DJI-203 in execution order with verification evidence.

**Architecture:** Backend-first stabilization and verification, then plugin completion; minimal invasive changes and explicit regression tests.

**Tech Stack:** Python/FastAPI/pytest and TypeScript/Obsidian/esbuild.

---

### Task 1: DJI-205 Batch Sync Optimization

**Files:**
- Modify: `daemon/sync_watcher.py`
- Add/Modify Test: `tests/test_sync_batch_optimization.py`

- [ ] Step 1: Add failing tests for batch embedding/upsert behavior and rel_path handling.
- [ ] Step 2: Run targeted test command and confirm failure.
- [ ] Step 3: Implement `embed_batch` + `batch_upsert` flow for markdown/canvas sync and compute `rel_path` once per file.
- [ ] Step 4: Re-run targeted tests and full pytest suite.
- [ ] Step 5: Update Linear `DJI-205` with verification evidence and move to Done.

### Task 2: DJI-202 Duplicate Hygiene

**Files:**
- No code changes expected unless uncovered by Task 1 verification.

- [ ] Step 1: Confirm all S20 acceptance criteria are satisfied by DJI-205 implementation/tests.
- [ ] Step 2: Post verification summary on `DJI-202` and leave state as Duplicate.

### Task 3: DJI-201 Security Hardening Closeout

**Files:**
- Modify: `tests/test_security_hardening.py`
- Optional Modify: `daemon/main.py` (only if failing tests reveal gaps)

- [ ] Step 1: Add failing tests for security headers and rate-limit behavior on sensitive routes.
- [ ] Step 2: Run targeted tests and confirm failures (or gaps).
- [ ] Step 3: Patch minimal backend behavior if needed.
- [ ] Step 4: Re-run targeted tests and full pytest suite.
- [ ] Step 5: Post evidence to `DJI-201` and move to Done.

### Task 4: DJI-200 Umbrella Issue Hygiene

**Files:**
- No repo code changes required unless documentation mismatch is found.

- [ ] Step 1: Update `DJI-200` comment/description with current child issue status and stale-note cleanup.
- [ ] Step 2: Keep `DJI-200` In Progress until all child scope is complete, then move to Done.

### Task 5: DJI-203 Full Obsidian Plugin Feature Delivery

**Files:**
- Modify: `obsidian-plugin/src/main.ts`
- Modify: `obsidian-plugin/src/components/DaemonClient.ts`
- Modify: `obsidian-plugin/src/views/SearchPanel.ts`
- Modify: `obsidian-plugin/src/views/GraphCanvas.ts`
- Modify: `obsidian-plugin/src/views/StatusBar.ts`
- Optional Add: `obsidian-plugin/src/settings/*` or utility files as needed
- Optional Modify: `obsidian-plugin/styles.css`, `obsidian-plugin/manifest.json`

- [ ] Step 1: Add plugin settings for daemon URL, API key, and sync preferences.
- [ ] Step 2: Implement search mode support (hybrid/topology/vector) in UI + client.
- [ ] Step 3: Implement ingest command flow for source->knowledge workflow.
- [ ] Step 4: Upgrade graph view to robust render/update behavior.
- [ ] Step 5: Implement auto-sync interval loop with safe lifecycle cleanup.
- [ ] Step 6: Build plugin (`npm install`, `npm run build`) and fix compile/runtime issues.
- [ ] Step 7: Update docs (`README.md`, `AGENTS.md`/`USER_GUIDE.md` as needed) to match new plugin behavior.
- [ ] Step 8: Post verification evidence to `DJI-203` and move to Done.

### Task 6: Final Verification and Branch Closeout

**Files:**
- Any modified files above

- [ ] Step 1: Run `python -m py_compile daemon/main.py daemon/pg_client.py daemon/sync_watcher.py daemon/retrieval.py`.
- [ ] Step 2: Run `python -m pytest tests/ -q --basetemp .pytest_tmp`.
- [ ] Step 3: Run plugin build verification in `obsidian-plugin`.
- [ ] Step 4: Update final statuses (`DJI-200` to Done if child scope complete).
- [ ] Step 5: Summarize changes, risks, and remaining constraints.
