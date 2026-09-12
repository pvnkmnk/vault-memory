---
name: fastapi-code-review
description: Review checklist for correctness, regressions, and missing tests in the vault-memory FastAPI/async codebase — async pitfalls, FastAPI/DI/auth checks, and test-quality signals.
metadata:
  category: quality
  language: python
  framework: fastapi
---

# Python FastAPI code review

## Review order

1. **Correctness of the change itself** — trace the data flow; check exception
   boundaries; verify every `await` is exception-safe where failure is expected.
2. **Regressions** — behavior changes must be intentional and documented in the
   commit message or PR body (e.g. "sync failure no longer fails the import").
3. **Missing tests** — every new branch in route code deserves a test; prefer
   behavioral tests via `app.dependency_overrides` over source-inspection tests.

## Async-specific checks

- **Single-flight tasks**: module-level `asyncio.Task` handles must be
  (a) exception-safe inside the coroutine (`try/except` + log, so the exception
  is always retrieved), (b) cleared in `finally` so a failed run re-arms,
  (c) loop-aware — compare `task.get_loop()` against `asyncio.get_running_loop()`
  before trusting `task.done()`, because a not-yet-done task from a dead loop
  is never done.
- **Check-then-assign races**: a bare check-then-assign on a module global is
  atomic only if there is no `await` between check and assign.
- **Fire-and-forget tasks**: `asyncio.create_task(...)` results must be kept
  referenced (assign to a variable/collection) or the task may be
  garbage-collected mid-flight.
- **Never `asyncio.run()` inside running loops**; in sync tests prefer
  `TestClient` or `asyncio.run` only at top level.

## FastAPI-specific checks

- Dependencies: `Depends(get_dependencies)` pattern; mock via
  `app.dependency_overrides[get_dependencies]`.
- Auth: `verify_api_key` reads `VAULT_MEMORY_API_KEY`; unset env = dev mode.
  Tests must monkeypatch-delete the key to avoid cross-test leakage.
- Error responses: 5xx must not leak `detail` (`error_response` handles this);
  expected user errors should be 4xx with a stable `code` string.
- Path handling: every file write keyed on user input goes through
  `_safe_vault_path`; validate that relative_to/resolve guards exist where new
  paths are constructed.

## Test-quality signals

- Source-inspection tests (`inspect.getsource`) are acceptable as cheap
  regression guards but must not be the only coverage for a branch.
- Symlink tests need `skipif(os.name == "nt")`.
- Tests that touch module-level state (`_bulk_jobs`, `_cleanup_task`) must save
  and restore the original values to avoid cross-test pollution.
- Regression tests should name the bug/PR they lock down in the docstring.
