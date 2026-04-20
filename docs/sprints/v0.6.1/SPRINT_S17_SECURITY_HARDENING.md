# Sprint S17: Security Hardening

**Version Target:** 0.6.1  
**Status:** PLANNED  
**Depends on:** S11, S12, S13, S14  
**Blocks:** None  
**Estimated files changed:** 3  
**Estimated lines changed:** 100  
**Risk:** MEDIUM  
**Assigned to:** TBD

## Goal
Implement security hardening measures including input validation, rate limiting, and security headers to prepare for production deployment.

## Findings Addressed
| ID | Source | Severity | Description |
|----|--------|----------|-------------|
| S17-A | Claude/Kimi | P1 | Input validation layer required |
| S17-B | Claude/Kimi | P1 | Rate limiting to prevent abuse |
| S17-C | Claude/Kimi | P2 | Security headers for protection |

## Changes

### daemon/validation.py (NEW)
- **What:** Create input validation utilities for paths, slugs, and text input
- **Why:** Prevent injection attacks and path traversal vulnerabilities
- **Lines:** Entire new file (~40 lines)
- **Rollback procedure:** Remove file

### daemon/main.py
- **What:** Integrate validation functions into all relevant endpoints
- **Why:** Ensure all inputs are properly sanitized before processing
- **Lines:** Validation calls in ingest, promote, search, etc. (~20 lines)
- **Rollback procedure:** Remove validation integrations

### daemon/main.py
- **What:** Implement rate limiting using slowapi
- **Why:** Prevent API abuse and denial of service
- **Lines:** Limiter setup and endpoint decorations (~15 lines)
- **Rollback procedure:** Remove rate limiting

### daemon/main.py
- **What:** Add security headers middleware
- **Why:** Protect against common web vulnerabilities
- **Lines:** Middleware function (~5 lines)
- **Rollback procedure:** Remove middleware

### pyproject.toml
- **What:** Add slowapi and other security dependencies
- **Why:** Required for rate limiting and security features
- **Lines:** [project.dependencies] section
- **Rollback procedure:** Remove dependencies

## Verification Steps
```bash
# Test input validation
# (Would require attempting malicious inputs)

# Test rate limiting
# (Would require making rapid requests to endpoints)

# Test security headers
curl -I http://localhost:5051/health | grep -E "X-Content-Type-Options|X-Frame-Options|X-XSS-Protection"
# Expected: Shows security headers present

# Run test suite
pytest tests/ -q
# Expected: Zero failures
```