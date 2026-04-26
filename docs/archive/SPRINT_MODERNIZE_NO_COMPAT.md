# Sprint: Remove Legacy Compatibility Paths (Pre-Launch Cleanup)

**Status:** PLANNED  
**Scope:** Whole codebase modernization before first deployment  
**Assumption:** No external clients exist yet, so breaking changes are acceptable if they simplify the runtime.

## Goal
Eliminate compatibility shims and fallback behavior that increase complexity, then standardize one canonical path per feature.

## Why This Sprint
The codebase still carries migration-era logic from multiple internal sprints. Since the system has not been deployed, keeping backward compatibility adds maintenance cost without user benefit.

## Findings (Legacy/Compatibility Debt)

| ID | Area | File | Current Legacy Behavior | Risk if Kept |
|---|---|---|---|---|
| L1 | DI | `daemon/dependencies.py` | Keeps legacy dependency getters (`get_weaviate`, `get_postgres`, etc.) alongside `Dependencies` container | Duplicate interface, inconsistent style |
| L2 | Globals | `daemon/main.py` | Keeps global service variables with “deprecated” comment while also using `app.state` | Confusing lifecycle ownership |
| L3 | Embedder API | `daemon/embedder.py` | Exposes sync wrappers `embed_*_sync`, `rerank_sync` “for backward compatibility” | Larger surface area than needed |
| L4 | MCP siblings | `cli/mcp_adapter.py` | `search_siblings` still has semantic-search fallback path when endpoint errors | Hides backend breakages |
| L5 | Bulk delete request | `daemon/main.py` | `BulkDeleteRequest` accepts both `paths` and legacy `ids` alias | Ambiguous contract |
| L6 | Internal state access | `cli/sync_command.py` | Writes `engine._state` directly | Couples CLI to private internals |
| L7 | Stale sprint comments | `daemon/main.py`, `daemon/sync_watcher.py`, `daemon/retrieval.py`, `cli/mcp_adapter.py` | Old P2/P3 sprint narrative and “preview” notes in production files | Documentation drift |
| L8 | Historical docs | `docs/P2_P3_SPRINT_PLAN.md`, AGENTS sprint notes | Historical plan docs mixed with current truth | Onboarding confusion |

## Proposed Modernization Plan

### S0: Contract Freeze (Breaking Change Declaration)
1. Declare canonical contracts:
- `bulk/delete` request field is `paths` only.
- `search_siblings` tool requires backend endpoint; no local fallback.
- DI uses `Dependencies` container only.
2. Update README/USER_GUIDE/AGENTS contract sections first.

### S1: Remove DI and Global Legacy Paths
1. `daemon/dependencies.py`
- Remove legacy helper functions (`get_weaviate`, `get_postgres`, `get_embedder`, `get_searcher`, `get_watcher`, `get_heartbeat`, `get_settings`).
2. `daemon/main.py`
- Stop importing removed helpers.
- Remove deprecated global state variables and `global ...` pattern in lifespan.
- Initialize services as local lifespan vars and assign directly to `app.state`.
- Update `_check_dependencies()` to read from `app.state` via dependency container or accessor function.

### S2: Narrow API Surfaces
1. `daemon/embedder.py`
- Keep only async public methods (`embed_batch`, `embed_one`, `rerank`).
- Convert sync helpers to private internals if still needed by executor (`_embed_batch`, `_embed_one`, `_rerank`).
2. `daemon/main.py`
- Remove `AliasChoices("paths", "ids")`; accept `paths` only.
- Return explicit 422 for legacy clients that send `ids`.

### S3: Remove Runtime Fallbacks That Mask Errors
1. `cli/mcp_adapter.py`
- Remove `search_siblings` semantic-search fallback branch.
- Fail fast with clear error if `/search_siblings` fails.
2. Keep health endpoints public, but require authenticated headers everywhere else as already implemented.

### S4: Public State Accessor for Sync Engine
1. `daemon/sync_watcher.py`
- Add read/write-safe `state` property or explicit methods (`clear_state`, `set_last_full_sync`) so callers never use `_state`.
2. `cli/sync_command.py`
- Stop direct `_state` usage; call new public methods/property.

### S5: Documentation and Comment Hygiene
1. Strip sprint-era comments from runtime modules.
2. Keep concise, behavior-centric docstrings only.
3. Move old sprint narratives to `docs/archive/` or remove if obsolete.

## Deliverables
1. Code cleanup PR with breaking-change notes.
2. Updated:
- `README.md`
- `USER_GUIDE.md`
- `AGENTS.md`
3. Optional: `docs/MIGRATION_BREAKING_CHANGES.md` (even if no users yet, good record).

## Verification Gates

### Build/Static
```bash
python -m py_compile daemon/main.py daemon/dependencies.py daemon/embedder.py daemon/sync_watcher.py cli/mcp_adapter.py cli/sync_command.py
```

### Tests
```bash
pytest tests/test_syntax.py tests/test_regex.py tests/test_sanitize.py tests/test_mcp_auth.py -q --basetemp .pytest_tmp
```

### Runtime Smoke
1. Daemon boots; `/health` and `/ready` return success.
2. MCP `search`, `search_siblings`, `memory/cognify` succeed with API key.
3. `vault-memory sync --full` and `vault-memory sync --check-drift` run without private-state access.
4. `/bulk/delete` works with `paths`; request with `ids` fails as expected.

## Suggested Execution Order
1. S0 + S1 (contract + DI/global cleanup)
2. S2 (API narrowing)
3. S3 (remove fallbacks)
4. S4 (sync state public API)
5. S5 (docs/comments)

## Exit Criteria
1. No compatibility aliases/fallbacks remain in core runtime paths.
2. Single canonical request/response contract per endpoint/tool.
3. No deprecated globals or legacy DI helpers in production code.
4. Tests + compile + smoke checks pass.
