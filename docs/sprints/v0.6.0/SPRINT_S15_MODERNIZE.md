# Sprint S15: Modernize

**Version Target:** 0.6.0  
**Status:** PLANNED  
**Depends on:** S10  
**Blocks:** None  
**Estimated files changed:** 3  
**Estimated lines changed:** 60  
**Risk:** LOW  
**Assigned to:** orchestrator

## Goal
Modernize the codebase with protocol bypass fixes, structured logging, topic hubs optimization, and improved test coverage.

## Findings Addressed
| ID | Source | Severity | Description |
|----|--------|----------|-------------|
| S15-A | Claude/Kimi | P2 | Topic hubs upsert pattern optimization |
| S15-B | Claude/Kimi | P1 | Protocol bypass fix for page_type validation |
| S15-C | Claude/Kimi | P2 | Structured JSON logging implementation |
| S15-D | Claude/Kimi | P2 | Test coverage improvements |

## Changes

### daemon/main.py
- **What:** Implement optimized topic hubs upsert pattern with temp table
- **Why:** Improve performance of topic hubs refresh operation
- **Lines:** refresh_topic_hubs function (~20 lines)
- **Rollback procedure:** Revert to original upsert implementation

### daemon/main.py
- **What:** Add protocol bypass fix for validate_protocol_page_type
- **Why:** Prevent invalid page_types from bypassing validation in promote/ingest
- **Lines:** Validation function (~5 lines)
- **Rollback procedure:** Remove validation function

### daemon/main.py
- **What:** Implement structured JSON logging with pythonjsonlogger
- **Why:** Enable machine-readable logs for monitoring and analysis
- **Lines:** StructuredLogFormatter class and logger configuration
- **Rollback procedure:** Revert to standard logging format

### tests/
- **What:** Add test files for all S10 fixes plus S11-S15 features
- **Why:** Ensure proper test coverage for reliability
- **Files:** test_wiki_layer.py, test_topology.py, test_token_efficiency.py, test_git_integration.py
- **Rollback procedure:** Remove added test files

### pyproject.toml
- **What:** Update dependencies and add test extras
- **Why:** Maintain current dependencies and enable testing
- **Lines:** [project.dependencies] and [project.optional-dependencies] sections
- **Rollback procedure:** Revert to original dependencies

## Verification Steps
```bash
# Run tests with coverage
pytest tests/ -v --cov=vault_memory --cov-report=html
# Expected: Coverage >80%

# Verify topic hubs performance improvement
# (Would require benchmarking)

# Test protocol validation
# (Would require attempting invalid page_type)

# Check structured logs in output
vault-memory daemon start 2>&1 | head -5
# Expected: JSON formatted log lines

# Run test suite
pytest tests/ -q
# Expected: Zero failures
```