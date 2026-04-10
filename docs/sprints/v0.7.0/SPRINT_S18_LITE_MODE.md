# Sprint S18: Lite Mode (SQLite-only)

**Version Target:** 0.7.0  
**Status:** PLANNED  
**Depends on:** S12  
**Blocks:** S19  
**Estimated files changed:** 4  
**Estimated lines changed:** 150  
**Risk:** MEDIUM  
**Assigned to:** TBD

## Goal
Create a lite mode variant that uses SQLite instead of PostgreSQL+Weaviate for embedded/disconnected usage.

## Findings Addressed
| ID | Source | Severity | Description |
|----|--------|----------|-------------|
| S18-A | Claude/Kimi | P1 | SQLite backend abstraction needed |
| S18-B | Claude/Kimi | P2 | Reduced feature set for lite mode |
| S18-C | Claude/Kimi | P2 | Installation profile for lite mode |

## Changes

### daemon/db_abstraction.py (NEW)
- **What:** Create database abstraction layer supporting both PostgreSQL and SQLite
- **Why:** Enable lite mode to run without external PostgreSQL dependency
- **Lines:** Entire new file (~60 lines)
- **Rollback procedure:** Remove file

### daemon/main.py
- **What:** Add lite mode configuration flag and conditional backend selection
- **Why:** Allow switching between full and lite modes via configuration
- **Lines:** Configuration initialization and backend selection (~20 lines)
- **Rollback procedure:** Remove lite mode conditionals

### daemon/sync_watcher.py
- **What:** Adjust sync logic for lite mode constraints (no vector search)
- **Why:** Lite mode cannot support vector-dependent features like cognify
- **Lines:** Sync watcher adaptations (~15 lines)
- **Rollback procedure:** Remove lite mode adaptations

### pyproject.toml
- **What:** Add lite mode optional dependencies: sqlite, reduced feature set
- **Why:** Provide lite mode installation option
- **Lines:** [project.optional-dependencies] lite mode section
- **Rollback procedure:** Remove lite mode dependencies

## Verification Steps
```bash
# Install lite mode
pip install "vault-memory[lite]"

# Test lite mode startup
VAULT_MEMORY_LITE_MODE=1 vault-memory daemon start
# Expected: Starts without PostgreSQL/Weaviate dependencies

# Test core functionality (search, etc. with limitations)
# Expected: Basic keyword search works, advanced features disabled

# Run test suite (lite mode subset)
pytest tests/ -q -k "lite"
# Expected: Lite mode tests pass
```