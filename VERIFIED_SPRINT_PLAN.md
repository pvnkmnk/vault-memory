# Vault Memory Sprint Plan

## Verified Issues Summary

### P0 - Critical (Must Fix First)

| Issue | File | Line(s) | Description |
|-------|------|---------|-------------|
| SQL Injection | daemon/main.py | ~50-80 | `session_list` and `search_siblings` endpoints use f-string interpolation with user input |
| No Authentication | daemon/main.py | all | All FastAPI endpoints lack auth middleware |
| Syntax Error | cli/mcp_adapter.py | ~932 | Broken f-string: `f"V2.0.0"` - extra quotes inside f-string |
| Syntax Error | cli/mcp_adapter.py | ~850 | Indentation issue around `cognify` call |
| Cursor Leak | daemon/main.py | ~45 | DB cursors not properly closed in exception handlers |
| Missing Import | daemon/retrieval.py | ~499 | Missing `from pathlib import Path` |

### P1 - High Priority

| Issue | File | Line(s) | Description |
|-------|------|---------|-------------|
| Double-escaped Regex | daemon/sync_watcher.py | 146-147 | `TAG_RE` and `STATUS_RE` have `\\` instead of `\` |
| Async/Sync Mismatch | daemon/sync_watcher.py | 567 | `flush_debounced` is async but called via `call_soon_threadsafe` |
| Config Priority Bug | daemon/config.py | 29-38 | Config file overrides env vars (should be reverse) |
| Version Mismatch | daemon/main.py vs config | - | Code shows 0.2.0, config shows 0.5.0-p3 |

### P2 - Medium Priority

| Issue | File | Line(s) | Description |
|-------|------|---------|-------------|
| Missing Schema Props | init_db.sql | - | Weaviate schema missing `importance`, `trust`, `maturity` |
| N+1 Query | daemon/main.py | ~search_siblings | Loop makes separate DB query per sibling |
| No Test Suite | - | - | Entirely missing test coverage |

### P3 - Low Priority / Tech Debt

| Issue | File | Line(s) | Description |
|-------|------|---------|-------------|
| GARS Scoring | - | - | Documented but not implemented |
| Accordion Context | - | - | Documented but not implemented |
| Drift Detection CLI | - | - | Documented but not implemented |
| Obsidian Plugin | - | - | Documented but not implemented |
| Heartbeat Scheduler | - | - | Documented but not implemented |
| Topic Hub Population | - | - | Documented but not implemented |

---

## Sprint Breakdown

### Sprint 1: P0 Critical Fixes
**Goal**: Fix critical bugs that cause runtime failures

1. Fix SQL injection in `daemon/main.py`
   - Replace f-string interpolation with parameterized queries
   - Files: `session_list`, `search_siblings`, any other endpoint

2. Fix syntax errors in `cli/mcp_adapter.py`
   - Line ~932: Fix `f"V2.0.0"` → `"V2.0.0"`
   - Line ~850: Fix indentation around cognify call

3. Fix missing import in `daemon/retrieval.py`
   - Add `from pathlib import Path`

4. Fix cursor leaks in `daemon/main.py`
   - Ensure cursors are closed in all code paths
   - Use context managers or try/finally

**Deliverables**: All P0 issues resolved, code runs without syntax/cursor errors

---

### Sprint 2: P1 High Priority Fixes
**Goal**: Fix bugs that cause incorrect behavior

1. Fix double-escaped regexes in `daemon/sync_watcher.py`
   - Line 146: `r"(?:^|\\s)#(\\[\\w/\\]+)"` → `r"(?:^|\s)#(\[\w/\]+)"`
   - Line 147: `r"status:\s*(\S+)"` → remove extra backslash

2. Fix async/sync mismatch in `daemon/sync_watcher.py`
   - Line 567: Replace `call_soon_threadsafe` with proper async call
   - Or make the callback truly async-safe

3. Fix config priority in `daemon/config.py`
   - Env vars should override config file
   - Current: config file wins (line 36-38)
   - Expected: env vars win

4. Resolve version mismatch
   - Align version across all files

**Deliverables**: All P1 issues resolved, regex and config work correctly

---

### Sprint 3: P2 Medium - Data & Queries
**Goal**: Improve data integrity and query performance

1. Add missing Weaviate schema properties
   - Add `importance`, `trust`, `maturity` to schema

2. Fix N+1 query pattern
   - Refactor `search_siblings` to use batch query

3. Set up test suite
   - Add pytest configuration
   - Write tests for critical paths (SQLi fixes, config, regex)

**Deliverables**: Tests exist, N+1 fixed, schema complete

---

### Sprint 4: P2 Features Implementation
**Goal**: Implement documented features that are missing

1. GARS Scoring
2. Accordion Context Assembly
3. Drift Detection CLI

**Deliverables**: Core features working

---

### Sprint 5: P3 - polish & Extras
**Goal**: Complete remaining documented features

1. Obsidian Plugin skeleton
2. Heartbeat Scheduler
3. Topic Hub Population
4. Structured logging improvements
5. Correlation IDs for tracing

**Deliverables**: All documented features either implemented or explicitly deferred

---

## Dependencies

```
Sprint 1 → Sprint 2 → Sprint 3 → Sprint 4 → Sprint 5
   ↑         ↑         ↑         ↑
   |         |         |         |
   +---- Authentication to be added after SQLi fixed
```

## Notes

- **Authentication** (P0) was marked but requires SQLi fixed first to be testable
- Sync watcher issues (P1) should be fixed before feature work on sync
- Tests (P2) should be written alongside code changes, not after
