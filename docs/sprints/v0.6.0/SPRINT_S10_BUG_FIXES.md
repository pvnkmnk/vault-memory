# Sprint S10: Bug Fixes

**Version Target:** 0.6.0  
**Status:** PLANNED  
**Depends on:** None  
**Blocks:** S11, S15  
**Estimated files changed:** 4  
**Estimated lines changed:** 50  
**Risk:** LOW  
**Assigned to:** orchestrator

## Goal
Fix critical bugs identified in S1-S9 audits to establish a stable foundation for v0.6.0 features.

## Findings Addressed
| ID | Source | Severity | Description |
|----|--------|----------|-------------|
| S10-A | Claude/Kimi | P1 | `_detect_drift` queries non-existent `name` column |
| S10-B | Claude/Kimi | P1 | `VaultSyncWatcher.stop()` blocks event loop |
| S10-C | Claude/Kimi | P1 | Audit logger `propagate=True` causes KeyError |
| S10-D | Claude/Kimi | P1 | `_persist_cognify_triples` uses unreliable `cursor.rowcount` |
| S10-E | Claude/Kimi | P1 | Canvas content uses literal `\\n` instead of newline |
| S10-F | Claude/Kimi | P1 | `pg_client._health_check()` connection pool leak |
| S10-G | Claude/Kimi | P1 | `_VaultEventHandler.on_deleted` double-queue bug |
| S10-H | Claude/Kimi | P1 | Path leakage in `bulk_delete` error responses |
| S10-I | Claude/Kimi | P1 | Dev-mode auth bypass logs per request |

## Changes

### daemon/sync_watcher.py
- **What:** Fix `_detect_drift` SQL query to remove non-existent `name` column
- **Why:** Prevents `psycopg2.errors.UndefinedColumn` when running `--check-drift`
- **Lines:** ~146-147 (TAG_RE, STATUS_RE fixes from S2 also apply here)
- **Rollback procedure:** Revert SQL to original form

### daemon/sync_watcher.py
- **What:** Make `stop()` method async-safe using `asyncio.to_thread`
- **Why:** Prevents blocking uvicorn event loop during shutdown
- **Lines:** ~567 (async/sync mismatch fix)
- **Rollback procedure:** Revert to synchronous `self._observer.join()`

### daemon/main.py
- **What:** Set `audit_logger.propagate = False` and add skip-list for health endpoints
- **Why:** Prevents KeyError in audit logging and reduces noise
- **Lines:** Logger configuration section
- **Rollback procedure:** Remove filter and reset propagate flag

### daemon/pg_client.py
- **What:** Fix `_persist_cognify_triples` to use `RETURNING` with `fetchall()`
- **Why:** `cursor.rowcount` is unreliable after `INSERT ... ON CONFLICT`
- **Lines:** Cognify triple persistence function
- **Rollback procedure:** Revert to original `cursor.rowcount` approach

### daemon/sync_watcher.py
- **What:** Fix canvas content generation to use actual newlines
- **Why:** Literal `\\n` strings were not being interpreted as newlines
- **Lines:** CanvasParser content generation
- **Rollback procedure:** Revert to literal `\\n` strings

### daemon/pg_client.py
- **What:** Fix `_health_check()` to properly return connections to pool
- **Why:** Connection pool leak causing exhaustion over time
- **Lines:** Health check method
- **Rollback procedure:** Revert to original health check implementation

### daemon/sync_watcher.py
- **What:** Cancel pending upserts in `_VaultEventHandler.on_deleted`
- **Why:** Prevents double-queue bug where deleted files get re-added
- **Lines:** Delete event handler
- **Rollback procedure:** Remove the pending upsert cancellation

### cli/sync_command.py
- **What:** Sanitize error messages in `bulk_delete` to prevent path leakage
- **Why:** Forbidden path validation errors were exposing internal paths
- **Lines:** Bulk delete error handling
- **Rollback procedure:** Revert to original error message handling

### daemon/main.py
- **What:** Move dev-mode API key warning to startup lifecycle (once-only)
- **Why:** Prevents log spam from per-request warnings in dev mode
- **Lines:** Lifespan event handler and verify_api_key dependency
- **Rollback procedure:** Move warning back to verify_api_key function

## Verification Steps
```bash
# Compile syntax check
python -m py_compile cli/sync_command.py daemon/sync_watcher.py daemon/main.py daemon/pg_client.py

# Test drift detection (should not throw UndefinedColumn)
vault-memory sync --check-drift --vault ~/ObsidianVault
# Expected: Drift table or "No drift detected"

# Test daemon start/stop speed (<2s)
vault-memory daemon start && time vault-memory daemon stop

# Test canvas newline handling
python -c "
from daemon.sync_watcher import CanvasParser
from pathlib import Path
import json, tempfile, os
with tempfile.NamedTemporaryFile(suffix='.canvas', mode='w', delete=False) as f:
    json.dump({'nodes':[{'id':'n1','text':'hello','file':'test.md'}],'edges':[]}, f)
    fname = f.name
nodes, _ = CanvasParser(Path('/tmp')).parse(Path(fname))
assert '\n\n' in nodes[0].content
os.unlink(fname)
print('Canvas newline OK')
"

# Test cognify triple persistence count accuracy
curl -s -X POST http://localhost:5051/cognify \
  -H "x-api-key: $VAULT_MEMORY_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"text": "Claude is made by Anthropic. Anthropic was founded in 2021.", "persist": true}' | \
  python -c "import sys,json; d=json.load(sys.stdin); assert d.get('entities_written',0)>0; print('PASS')"

# Verify audit logger skips health endpoints
vault-memory health && grep -c '/health' ~/.vault-memory/daemon.log
# Expected: 0

# Run test suite
pytest tests/ -q --tb=short
# Expected: Zero failures
```