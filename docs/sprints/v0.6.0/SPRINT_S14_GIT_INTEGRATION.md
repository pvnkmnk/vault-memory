# Sprint S14: Git Integration

**Version Target:** 0.6.0  
**Status:** PLANNED  
**Depends on:** S11  
**Blocks:** S17  
**Estimated files changed:** 4  
**Estimated lines changed:** 100  
**Risk:** MEDIUM  
**Assigned to:** orchestrator

## Goal
Integrate git capabilities for branch-aware sessions, incremental sync based on commits, and git hook installation for automatic synchronization.

## Findings Addressed
| ID | Source | Severity | Description |
|----|--------|----------|-------------|
| S14-A | Claude/Kimi | P1 | GitContext class for vault git operations |
| S14-B | Claude/Kimi | P1 | Diff-based incremental sync since commit |
| S14-C | Claude/Kimi | P2 | Git hooks CLI for automatic sync |
| S14-D | Claude/Kimi | P2 | Branch-aware agent sessions |

## Changes

### daemon/git_integration.py (NEW)
- **What:** Implement GitContext class with branch, commit, and file change detection
- **Why:** Enable git-aware operations throughout the daemon
- **Lines:** Entire new file (~50 lines)
- **Rollback procedure:** Remove file

### daemon/sync_watcher.py
- **What:** Add GitContext dependency and integrate with sync operations
- **Why:** Enable git-aware file watching and processing
- **Lines:** Watcher initialization and event handling
- **Rollback procedure:** Remove GitContext integration

### init_db.sql
- **What:** Add last_git_commit column to sync_state table
- **Why:** Track last synced commit for incremental sync
- **Lines:** ALTER TABLE statement
- **Rollback procedure:** Drop added column

### cli/main.py
- **What:** Implement git hooks install command
- **Why:** Provide CLI for installing post-commit/checkout/merge hooks
- **Lines:** Hooks group and install command (~20 lines)
- **Rollback procedure:** Remove hooks CLI

### init_db.sql
- **What:** Add git_branch and git_commit columns to agent_sessions table
- **Why:** Enable branch-aware session tracking
- **Lines:** ALTER TABLE statements
- **Rollback procedure:** Drop added columns

## Verification Steps
```bash
# Install with git extras
pip install "vault-memory[git]"

# Initialize git repo and install hooks
cd ~/ObsidianVault && git init
vault-memory hooks install --vault ~/ObsidianVault

# Test hook triggers
echo "test" >> test.md && git add test.md && git commit -m "test"
# Hook must trigger sync attempt

# Test branch-aware sessions
curl http://localhost:5051/sessions | python -m json.tool | grep git_branch
# Expected: Shows current git branch in sessions

# Run test suite
pytest tests/ -q
# Expected: Zero failures
```