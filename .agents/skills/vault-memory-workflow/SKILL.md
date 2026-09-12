---
name: vault-memory-workflow
description: Commands, architecture conventions, known gotchas, and testing patterns for working in the vault-memory Python daemon repository (FastAPI + Click + Postgres/Weaviate, pytest with mocked heavy deps).
metadata:
  category: development
  language: python
  framework: fastapi
---

# vault-memory development workflow

## What this repo is

Python 3.11+ FastAPI daemon (`daemon/`) + Click CLI (`cli/`) providing a semantic
memory layer for Obsidian vaults. Postgres + Weaviate in full mode, SQLite-only
in Lite Mode. Tests use pytest with heavy deps (sentence_transformers, psycopg2)
mocked in `tests/conftest.py`.

## Commands

```bash
python3 -m py_compile daemon/main.py cli/mcp_adapter.py   # quick syntax check
pytest tests/ -q --basetemp .pytest_tmp                    # full unit suite
pytest tests/test_<file>.py -q --basetemp .pytest_tmp      # targeted
```

- Local sandbox may have Python 3.10 even though production targets 3.11+ —
  write 3.10-compatible syntax in tests; never rely on 3.11-only features in
  test code.
- `python` may not exist on PATH; use `python3`.
- Integration tests that need live Postgres/Weaviate/Ollama are skipped in
  lightweight environments (expect ~7 skips). "passed + skipped" = green.

## Architecture conventions

- **DI container**: routes take `deps: Dependencies = Depends(get_dependencies)`
  and access services via typed properties (`deps.postgres`, `deps.watcher`,
  `deps.settings`). Never reach for module globals in endpoint code. The CLI
  has its own mirror container in `cli/dependencies.py`.
- **Route files**: `daemon/routes/<domain>.py` exporting a `<domain>_router`;
  `daemon/main.py` imports and includes them. Health lives in `daemon/health.py`.
- **Error responses**: use `daemon/helpers/responses.py` (`bad_request`,
  `server_error`). `error_response` HIDES `detail` for all 5xx — never leak
  technical error text in server-error paths.
- **Path safety**: all vault-relative writes MUST go through
  `_safe_vault_path(vault_root, rel)` from `daemon/helpers/validation.py`
  (rejects absolute paths, `..` traversal, and symlink escapes via
  `os.path.commonpath`). `_canonicalize_vault_root` resolves the root.
- **Async DB access**: use `with deps.postgres.cursor() as cursor:` context
  managers; never leak cursors.
- **In-memory background work**: single-flight pattern with a module-level task
  handle (`_cleanup_task` in `daemon/routes/bulk.py`) — see
  `_spawn_cleanup_if_idle` for the loop-safe check-then-assign idiom.

## Known gotchas (from AGENTS.md history)

- `init_db.sql` uses `id` as PK for sessions, not `session_id`.
- Env vars have the HIGHEST config priority (over config file) — `daemon/config.py`.
- Cleanup queries filter on `started_at`/`last_ping_at`, never `registered_at`.
- Watcher sync failures after a successful disk write are logged, not fatal —
  the note still counts as imported.
- Version must stay aligned between `pyproject.toml` and `daemon/version.py`
  (enforced by `tests/test_version_alignment.py`).

## Testing patterns

- Mock `Dependencies` with `MagicMock(spec=Dependencies)` (see
  `tests/conftest.py::mock_dependencies`).
- For route tests against the real app: override
  `app.dependency_overrides[get_dependencies] = lambda: deps`, wrap
  `TestClient(app, raise_server_exceptions=False)` in
  `with patch("daemon.main.lifespan", MagicMock()):`, and clear overrides in
  `finally`. Delete `VAULT_MEMORY_API_KEY` via monkeypatch for dev-mode auth.
- Call async handlers directly with `asyncio.run(handler(req, deps=deps, _auth=None))`
  for focused unit tests without the full app.
- Prefer behavioral tests (dependency overrides + real handler) over
  source-inspection tests; keep source-inspection asserts only as cheap guards.
- Symlink-based tests: mark with
  `@pytest.mark.skipif(os.name == "nt", ...)` — Windows CI may lack symlink
  privileges.
