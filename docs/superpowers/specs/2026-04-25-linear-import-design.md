# Linear Import Design — vault-memory Sprint Tracker

**Date:** 2026-04-25
**Author:** orchestrator
**Status:** Approved

## Overview

Import the vault-memory sprint documentation (Conductor Master + sprint docs) into Linear as issues, projects, and cycles, preserving the existing DJI-### ticket numbering and sprint structure. Linear becomes the single source of truth for all sprint tracking.

## Workspace Structure

### Teams

- **Vault_Memory** (VAU) — existing, ID `9d788748-97d6-43f6-b978-be783010d6e5`

### Projects

| Project | Name | Description | Status |
|---------|------|-------------|--------|
| VAU-1 | v0.8.0 | Batch optimization + plugin enhancements | Active |
| VAU-2 | v0.9.0 | Mobile companion, collaborative editing, Canvas integration | Backlog |
| VAU-3 | Backlog | Future sprints beyond S23 | Backlog |

### Cycles (Sprints)

> **Note:** Linear's `CycleCreateInput` does not include a `projectId` field, and `CycleUpdateInput` does not support `projectId` either. Cycles are team-scoped and cannot be programmatically linked to projects via the API. Assign cycles to projects manually in the Linear UI (Project → Settings → Cycles), or via project settings.

| Cycle | Project | Name | Dates | Status |
|-------|---------|------|-------|--------|
| CYC-1 | VAU-1 | S20 Batch Optimization | 2026-07-27 → 2026-08-09 | Completed |
| CYC-2 | VAU-1 | S20 Enhancements | 2026-08-10 → 2026-08-23 | Upcoming |
| CYC-3 | VAU-2 | S21 Mobile Companion App | 2026-08-24 → 2026-09-13 | Planned |
| CYC-4 | VAU-2 | S22 Collaborative Editing | 2026-09-14 → 2026-10-04 | Planned |
| CYC-5 | VAU-2 | S23 Obsidian Canvas Integration | 2026-10-05 → 2026-10-25 | Planned |

### Labels

| Label | Color | Description |
|-------|-------|-------------|
| `priority/p0` | `#EB5757` | Critical — must fix first |
| `priority/p1` | `#F97316` | High priority |
| `priority/p2` | `#EAB308` | Medium priority |
| `priority/p3` | `#22C55E` | Low priority / tech debt |
| `sprint/s20-batch` | `#4EA7FC` | Sprint S20 batch optimization |
| `sprint/s20-enhancements` | `#4EA7FC` | Sprint S20 plugin enhancements |
| `sprint/s21` | `#4EA7FC` | Sprint S21 |
| `sprint/s22` | `#4EA7FC` | Sprint S22 |
| `sprint/s23` | `#4EA7FC` | Sprint S23 |
| `type/feature` | `#BB87FC` | New feature |
| `type/improvement` | `#4EA7FC` | Improvement |
| `type/bug` | `#EB5757` | Bug fix |
| `type/docs` | `#6B7280` | Documentation |

## Issue Catalog

### S20 Batch Optimization (6 issues — all Done)

All in Cycle CYC-1, State: Done, Labels: `sprint/s20-batch`

| ID | Title | Priority | Files | Key Implementation |
|----|-------|----------|-------|---------------------|
| DJI-253 | S20-A: Add sync configuration parameters | P1 | daemon/config.py | 4 new config options: SYNC_CONCURRENCY, EMBED_BATCH_SIZE, STATE_WRITE_BATCH, STATE_WRITE_TIMEOUT_S |
| DJI-254 | S20-B: Dynamic embedding batch sizing | P1 | daemon/embedder.py | GPU detection via torch + nvidia-smi fallback, auto-sizes batch 16→256 based on VRAM |
| DJI-255 | S20-C: Parallel file processing | P1 | daemon/sync_watcher.py | asyncio.Semaphore worker pool, 10 concurrent syncs, 20-file memory-efficient batches |
| DJI-256 | S20-D: Parallel Weaviate batch ingestion | P1 | daemon/weaviate_client.py | Parallel batch_upsert with Semaphore(5), WEAVIATE_BATCH_SIZE=100 |
| DJI-257 | S20-E: State file write batching | P1 | daemon/sync_watcher.py | Threshold/timeout flush queue, reduces 1000 writes → ~100 writes |
| DJI-258 | S20-F: Sync performance benchmarks | P2 | tests/test_sync_batch_optimization.py | 6 benchmark tests targeting 15+ files/sec throughput |

**All 6 issues: Labels include `type/improvement`**

### S20 Enhancements (6 issues — 5 Done, 1 Planned)

All in Cycle CYC-2, Labels: `sprint/s20-enhancements`

| ID | Title | Priority | State | Lines | Key Features |
|----|-------|----------|-------|-------|---------------|
| DJI-259 | S20-A: Expose missing MCP tools in DaemonClient | P1 | ✅ Done | +120 | promote, cognify, lint, bulkImport, bulkExport, session methods |
| DJI-260 | S20-B: Add bulk/promote/cognify UI to SearchPanel | P1 | ✅ Done | +80 | Bulk import/export buttons, promote, cognify, vault lint trigger |
| DJI-261 | S20-C: Daily note integration (DailyNotesView) | P1 | ✅ Done | +100 | Templated daily notes with context, today/yesterday links, mood/productivity capture |
| DJI-262 | S20-D: Outgoing links panel in GraphCanvas | P2 | ✅ Done | +60 | Click node → sidebar with backlinks/outlinks, filter by relationship type, PNG/SVG export |
| DJI-263 | S20-E: Community plugin marketplace README | P3 | ✅ Done | +150 | Screenshots/demo GIF, BRAT install, feature list, changelog, support links |
| DJI-264 | S20-F: Automated release workflow | P3 | Todo | +60 | .github/workflows/release.yml for marketplace publishing |

### S21 Mobile Companion App (6 issues — all Planned)

All in Cycle CYC-3, State: Todo, Labels: `sprint/s21`

| ID | Title | Priority |
|----|-------|----------|
| DJI-265 | S21-A: Mobile-first responsive layout | P1 |
| DJI-266 | S21-B: Touch-optimized search panel | P1 |
| DJI-267 | S21-C: Swipe gestures for graph navigation | P2 |
| DJI-268 | S21-D: Offline-first sync queue | P1 |
| DJI-269 | S21-E: Notification framework for sync events | P2 |
| DJI-270 | S21-F: Performance benchmarks for mobile | P2 |

### S22 Collaborative Editing (6 issues — all Planned)

All in Cycle CYC-4, State: Todo, Labels: `sprint/s22`

| ID | Title | Priority |
|----|-------|----------|
| DJI-271 | S22-A: CRDT-based merge strategy | P1 |
| DJI-272 | S22-B: Conflict resolution UI | P1 |
| DJI-273 | S22-C: Session-based locking mechanism | P2 |
| DJI-274 | S22-D: Real-time sync WebSocket endpoint | P1 |
| DJI-275 | S22-E: Operational transform for markdown | P2 |
| DJI-276 | S22-F: Collaborative editing benchmarks | P2 |

### S23 Obsidian Canvas Integration (4 issues — all Planned)

All in Cycle CYC-5, State: Todo, Labels: `sprint/s23`

| ID | Title | Priority |
|----|-------|----------|
| DJI-277 | S23-A: Canvas file parser improvements | P1 |
| DJI-278 | S23-B: Node relationship extraction from Canvas | P1 |
| DJI-279 | S23-C: Canvas to knowledge graph pipeline | P2 |
| DJI-280 | S23-D: Canvas rendering in VaultPortal plugin | P2 |

### Sprint Modernize No Compat (8 issues — all Planned)

Not yet in Linear. Pre-launch compatibility cleanup before v1.0. Labels: `type/feature`, `type/docs`

| ID | Title | Priority | Description |
|----|-------|----------|-------------|
| DJI-281 | L1: Remove deprecated sync_batch_size | P2 | Remove `sync_batch_size` config; use `sync.concurrency` instead |
| DJI-282 | L2: Drop legacy SQLite compat layer | P2 | Remove SQLite backend compat shims; only support real backends |
| DJI-283 | L3: Remove v0.5.0 API routes | P1 | Remove `/api/v0.5/*` legacy endpoints |
| DJI-284 | L4: Migrate to pydantic v2 | P2 | Update all models from pydantic v1 to v2 (BaseModel → BaseModel) |
| DJI-285 | L5: Standardize exception hierarchy | P2 | Consolidate custom exceptions under `vault_memory.exceptions` |
| DJI-286 | L6: Remove CLI v1 flags | P1 | Remove `--compact`, `--deep`, `--force` legacy CLI flags |
| DJI-287 | L7: Enforce Python 3.11+ | P2 | Add runtime version check; remove 3.10 compatibility code |
| DJI-288 | L8: Update CHANGELOG for v1.0 | P3 | Write comprehensive v0.7→v1.0 changelog with breaking changes section |

## API Notes

- `issueLabelCreate` returns `{ success: Boolean }`, not `{ label: ... }`
- `cycleCreate` does NOT accept `state` or `projectId` — cycles are created with default state
- `cycleUpdate` does NOT accept `state` or `projectId` — cannot update cycle state or project via API
- Cycle dates must be in the future relative to the current date
- `cycleDelete` mutation is not available — clean up test artifacts manually in Linear UI

## Issue Description Template

Each issue description follows this structure:

```
## Summary
[One-line description]

## Changes
[Key files modified, bullet list]
- `{file}` — {what changed}

## Implementation Details
[Key technical decisions, bullet list]
- {decision}

## Verification
[How to verify the change works]
```bash
{verification command}
```

## Linked
docs/sprints/{path}
```

## Import Sequence

**Completed steps (2026-04-26):**
1. ✅ Created labels: priority/p0, priority/p1, priority/p2, priority/p3, sprint/s20-batch, sprint/s20-enhancements, sprint/s21, sprint/s22, sprint/s23, type/docs
2. ✅ Created projects: v0.8.0 (5759dfaa-...), v0.9.0 (4e29347f-...), Backlog (de938f5d-...)
3. ✅ Created cycles: CYC-1 through CYC-5 with dates 2026-07-27 → 2026-10-25

**Remaining steps:**
4. Import S20 Batch issues (DJI-253→258) — Done state, 6 issues via `issueBatchCreate` with `stateId`
5. Import S20 Enhancement issues (DJI-259→264) — DJI-259→263 already Done; update DJI-264 to Done when release workflow is implemented
6. Import S21 issues (DJI-265→270) — Todo state, 6 issues
7. Import S22 issues (DJI-271→276) — Todo state, 6 issues
8. Import S23 issues (DJI-277→280) — Todo state, 4 issues
9. Import Sprint Modernize issues (DJI-281→288) — Todo state, 8 issues (pre-launch compat cleanup)
10. Assign all issues to their respective cycles via `issueUpdate` with `cycleId`
11. Link cycles to projects manually in Linear UI (Project → Settings → Cycles)
12. Update CYC-2 (S20 Enhancements) state to Completed once DJI-264 is done

## External References

- Conductor Master: `docs/sprints/CONDUCTOR_MASTER.md`
- Sprint Modernize No Compat: `docs/archive/SPRINT_MODERNIZE_NO_COMPAT.md`

> Individual sprint detail docs (S10-S20) have been archived. Sprint status is tracked in Linear.

## Scope Boundaries

This import covers:
- All sprint docs from S20 onward (completed + planned)
- DJI numbering continuity from existing sprint docs
- Conductor Master project/sprint structure

This import does NOT cover:
- Historical sprints S1–S19 (already completed, documentation preserved in `docs/sprints/`)
- P0–P3 issues from VERIFIED_SPRINT_PLAN.md (those were one-time audit fixes, not recurring sprints)
- Existing backlog items not mentioned in sprint docs