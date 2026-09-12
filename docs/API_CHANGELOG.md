# API Changelog

All changes to the vault-memory REST API, tracked by version.

---

## [0.9.0] — 2026-09-12

### Added

- `POST /sessions/mine` — drain the session-mining queue once; distils closed sessions into `_working/sessions/*.md` lesson drafts (S31-3, #77). Also `vault-memory sessions mine`.
- `PATCH /sessions/{id}` now accepts a structured `session_record` (`decisions`, `mistakes`, `discoveries`, `gotchas`, `workflows`, each `{content, entities}`), and re-arms the mining queue (`mined_at = NULL`) when a session is reopened (S31-2, #76).
- `GET /lessons` — ranked promoted lessons for a project (recency × corroboration × trust) with a token-budgeted body section (S31-5, #79).
- `GET /lessons/review` — mined drafts awaiting a decision, plus the active auto-promote policy (S31-4, #78).
- `POST /lessons/promote` — accept a draft into `lessons/` (`review: approved`, maturity `seed` → `sapling`).
- `POST /lessons/reject` — reject a draft; the **reason is persisted** and injected into the next mining prompt for that project.
- `POST /lessons/auto-promote` — apply the `SESSION_MINING_AUTO_PROMOTE` policy (`off` | `conservative` | `aggressive`) to the pending queue.
- `POST /ingest` — ingest a vault-relative path, an `http(s)` URL (readability-extracted to markdown), or pasted text. Sources are archived immutably under `raw/{date}-{slug}.md` and compiled into `Knowledge/` pages with claim provenance (S32-1, #81).
- `POST /ingest/inbox` — drain the vault's `inbox/` directory.
- `GET /ingest/manifest` — the content-hash delta index of ingested sources.
- `POST /digest/{daily|weekly|monthly}` — build a digest into `digests/`; `monthly` also stages skill proposals under `_working/consolidation/` (S32-2/3/4, #82–#84).
- `GET /digest` — digests already on disk.
- `POST /skills/export` — publish corroborated lessons as agentskills-compatible `skills/<theme>/SKILL.md` bundles (S32-5, #85).
- `GET /skills` — exported skill bundles.
- MCP tools: `memory/ingest`, `memory/lesson_review`, `memory/lesson_promote`, `memory/lesson_reject` (22 tools total).
- CLI: `vault-memory ingest`, `vault-memory lessons {list,review,promote,reject}`, `vault-memory digest {daily,weekly,monthly}`, `vault-memory skills {export,list}`.
- Schema: `sync_log` (was queried but never created), `agent_sessions.session_record`, `agent_sessions.mined_at`, `agent_sessions.mining_error`.
- Lint rules: `lesson_conflicts` (a mined lesson touching an entity the graph records as contradictory) and `speculative_pages` (ingested pages whose claims are ≥50% `ambiguous`). Both are reported in `/lint` and in the lint report's summary; neither rewrites a page.
- `GET /lint` output gains `lesson_conflicts` and `speculative_pages`.
- `memory/project_state` gains a `lessons` field (plus `lessons_error`, `lesson_top_k`, and `lesson_token_budget` inputs).
- Optional dependency extra `vault-memory[ingest]` for PDF text extraction (`pypdf`); a PDF without it fails with a message naming the extra rather than a traceback.
- Config: `SESSION_MINING`, `SESSION_MINING_AUTO_PROMOTE`, `SESSION_MINING_SYNTHESIS_MODEL`, `DIGESTS`, `INGEST_ALLOW_PRIVATE_URLS`.
- SSRF guard on `POST /ingest`: `INGEST_URL_ALLOWLIST` (comma-separated hosts) restricts ingestion to named hosts when set; otherwise URLs pointing at loopback, private, link-local, or reserved addresses (including cloud metadata endpoints) are refused, both as literals and after DNS resolution. Set `INGEST_ALLOW_PRIVATE_URLS=1` to ingest from a host on the local network. Local paths are confined to the vault inside `daemon/ingest.py` (each path component must be its own basename, then the realpath is prefix-checked), so containment does not depend on the HTTP boundary.

### Changed

- `decay-profile: log` is now a registered decay profile (180 days). It was written by the miner but absent from `DECAY_PROFILES`, so lessons silently decayed at the 30-day `active` rate.
- `/sessions/{id}/attribution` returns `404` for an unknown session id instead of an empty payload.
- `/promote` no longer imports the never-built `daemon/validate_write` (which made it a guaranteed 500 in any non-lite deployment). Near-duplicate detection is replaced by lesson-level corroboration matching, which is what the design called for.
- MCP tool modules read `cli.mcp_client._auth_headers` at call time. Binding it by value at import meant any rebind left every tool calling the daemon unauthenticated.
- `/promote` refuses to overwrite a high-trust page, and reports the conflict.
- Version bumped to 0.9.0.

- `/cognify` LLM backend is now provider-switchable via `LLM_PROVIDER` env var: `ollama` (default, unchanged behavior) or `llamacpp` (any OpenAI-compatible endpoint such as llama.cpp `llama-server`). New env vars: `LLM_PROVIDER`, `LLAMACPP_URL` (default `http://localhost:8081`), `LLAMACPP_MODEL` (optional). Response shape is unchanged (`triples`, `invalid_triples`, `model`, `persistence`); the `model` field now reports the provider's model name. With `llamacpp`, `/cognify` requests the object-wrapped triple format (`{"triples": [...]}`) to match `json_object` response mode, and the parser accepts both wrapped and top-level-array responses; a one-time warning is logged if `LLAMACPP_MODEL` is empty (vLLM and some LM Studio configs require an explicit model).
- `/cognify` unavailable error message is provider-neutral (`LLM provider unavailable`); error code `OLLAMA_UNAVAILABLE` retained for backward compatibility.
- `/cognify` triple-extraction robustness for small models: Ollama requests pin `temperature: 0` (inside `options`, as Ollama requires) for deterministic output, and the response parser additionally accepts a bare single-triple JSON object (common small-model output) in addition to arrays and `{"triples": [...]}` wrappers.
- `/cognify` LLM request timeout is configurable via `LLM_TIMEOUT_SECONDS` (default 120s, previously hardcoded 30s). Small CPU-only models in `json_object` mode routinely need 30–90s, so the old timeout caused spurious failures on lean systems using the `llamacpp` provider.
- Fixed: `/cognify` persistence no longer fails with `persisted: false` when the LLM returns the same triple more than once in a single response — duplicate rows now deduplicate against the `uq_relationships_pair` constraint (`ON CONFLICT DO NOTHING`) instead of aborting the transaction.

### Added

- Linear integration: `scripts/linear-sync.js` (`doctor` / `pull` / `push-github`) syncing Linear Team VAU with the repo — `pull` maintains a committed mirror at `docs/LINEAR_MIRROR.md` (nightly via `.github/workflows/linear-mirror.yml`), `push-github` imports open GitHub issues into Linear idempotently under the v0.9.0 project. Auth via `LINEAR_API_KEY`.
- `docs/PRD.md` — product requirements document capturing the solidified vision: the learning loop (session mining S31 + human ingestion S32 + digest cadence), tracked as milestone v0.9.0 (issues #75–#85).
- `docs/DESIGN_BOUNDARIES.md` — evidence-based non-goals and scope limits (no cloud, no silent contradiction merge, no uncalibrated confidence scores, no OKF conformance yet, etc.).
- CI: GitHub Actions integration workflow (`.github/workflows/integration.yml`) running the suite against real PostgreSQL 16 + Weaviate 1.36.8 + Ollama service containers plus a step-started llama.cpp `llama-server` (same image/GGUF/flags as the Compose `llm` profile), including end-to-end `/cognify` tests (`tests/test_cognify_e2e.py`) that drive the FastAPI route through **both** providers (`LLM_PROVIDER=ollama` and `llamacpp`) and verify response→database persistence fidelity in real PostgreSQL.
- `/cognify` config knob: `LLM_TIMEOUT_SECONDS` (default 120).
- Docker Compose: optional `llm` profile (`docker compose --profile llm up -d`) adding Ollama (port 11434, persistent model volume) and llama.cpp `llama-server` (OpenAI-compatible, port 8081, GGUF from `./models`) so the local stack matches the CI integration environment.
- Native-binary (no-Docker) setup recipe for PostgreSQL + Weaviate integration tests in CONTRIBUTING.md.

---

## [0.8.0] — 2026-04-30

### Added

- `POST /sync/file` — Manually trigger sync of a single file (was documented but missing; implemented in 0.8.0)
- `POST /sync/delta` — Incremental sync since timestamp with pagination and `force_full` support (S26-1)
- `POST /bulk/queue` — Queue bulk import jobs, returns `job_id` immediately (S26-2)
- `GET /bulk/status/{job_id}` — Poll bulk job progress with percentage (S26-2)
- `DELETE /bulk/cancel/{job_id}` — Cancel queued or processing bulk jobs (S26-2)
- `POST /bulk/export?stream=true` — NDJSON streaming export for bounded memory (S26-3)
- `GET /me/usage` — Rate limit usage stats for authenticated API key (S26-4)
- `GET /health/detailed` — Comprehensive subsystem health dashboard (S28-4)
- `POST /sessions/cleanup` — Manually trigger stale session cleanup (S28-1)
- `GET /sessions/{session_id}/attribution` — All content attributed to a session (S28-3)
- `/docs` — Swagger UI (enabled via `VAULT_MEMORY_ENABLE_DOCS=1`) (S26-5)
- `/redoc` — ReDoc documentation (enabled via `VAULT_MEMORY_ENABLE_DOCS=1`) (S26-5)
- `/openapi.json` — Raw OpenAPI 3.0 spec (enabled via `VAULT_MEMORY_ENABLE_DOCS=1`) (S26-5)
- `GET /graph?source=canvas` — Filter graph to Canvas-derived relationships only (S27-1)
- `X-RateLimit-Limit` and `X-RateLimit-Remaining` headers on all responses (S26-4)

### Changed

- Rate limiter now tracks by API key instead of IP (S26-4)
- `/promote` now validates content against high-trust notes before writing (S28-2)
- `edge_source` CHECK constraint expanded to include `'canvas'` (S27-1)
- Heartbeat cycle now includes stale session cleanup (S28-1)
- Version bumped to 0.8.0 in FastAPI app metadata
- Endpoint summary now includes previously undocumented `/bulk/import` and `/bulk/delete`

### Deprecated

- `GET /bulk/export` (full-mode) — use `POST /bulk/export?stream=true` instead (S26-3)

### Schema Changes

- Added `canvas_entities` table for Canvas-extracted entities (S27-1)
- Added `notes TEXT` column to `agent_sessions` (S28-3)

---

## [0.7.0] — 2026-04-25

### Added

- Lite mode (SQLite-only, no Docker required)
- Connection pooling for PostgreSQL (ThreadedConnectionPool)
- Formal DI container (`Dependencies` class)
- API key authentication (`VAULT_MEMORY_API_KEY`)
- Correlation ID middleware for request tracing
- HeartbeatService wrapper for background jobs
- Health router with `/health`, `/ready` endpoints
- `POST /sync/delta` endpoint for mobile sync
- Bulk operations queue (in-memory)
- Streaming bulk export (NDJSON)
- Per-client rate limiting
- OpenAPI documentation

### Changed

- Version aligned between `pyproject.toml` and runtime code (0.7.0)
- Env vars now highest priority (override config file)
- `search_siblings` SQL uses `ANY(%s)` list semantics
- Rate limiter performs periodic stale-key eviction
- Dev-mode API key warning moved to startup lifecycle log
- Audit middleware skips `/health`, `/ready`, `/metrics`
- `bulk_delete` redacts forbidden path validation errors
- Canvas parser uses real newlines for file-node content
- Postgres pool health-check returns connection to original pool on reinit
- Delete watcher events cancel pending upserts for same path
- Ripgrep fast-path only short-circuits for likely path/filename exact queries

### Fixed

- DI regression in `/temporal` endpoint
- `_check_dependencies` embedder health check
- `_sanitize_for_context` regex escaping
- `TAG_RE` double-escape fix
- Delete watcher thread handoff
- Rate-limit burst window
- `/cognify` switched to non-blocking `httpx.AsyncClient`
- PG health check connection return hardened
- Broken bulk endpoints (no longer query non-existent `notes` table)
- Syntax test collection
- Regex assertions made meaningful
- `mock_dependencies` uses `MagicMock(spec=Dependencies)`
- `docker-compose.yml` includes explicit resource limits

---

## [0.6.1] — 2026-04-15

### Added

- Observability layer (metrics, logging, tracing)
- Security hardening (argument injection fixes, error redaction)

---

## [0.6.0] — 2026-04-01

### Added

- Wiki layer with promotion workflow
- Topology-aware search
- Token-efficient context assembly
- Git integration
- Modernization pass

---

## Endpoint Summary (v0.8.0)

### Search & Retrieval

| Method | Endpoint | Since |
|--------|----------|-------|
| `POST` | `/search` | 0.1.0 |
| `POST` | `/search_siblings` | 0.6.0 |
| `GET` | `/graph` | 0.1.0 |
| `GET` | `/temporal` | 0.1.0 |

### Sync

| Method | Endpoint | Since |
|--------|----------|-------|
| `POST` | `/sync/file` | 0.1.0 |
| `POST` | `/sync/delta` | 0.8.0 |

### Bulk Operations

| Method | Endpoint | Since |
|--------|----------|-------|
| `POST` | `/bulk/queue` | 0.8.0 |
| `GET` | `/bulk/status/{job_id}` | 0.8.0 |
| `DELETE` | `/bulk/cancel/{job_id}` | 0.8.0 |
| `POST` | `/bulk/import` | 0.1.0 |
| `POST` | `/bulk/export` | 0.1.0 |
| `POST` | `/bulk/export?stream=true` | 0.8.0 |
| `POST` | `/bulk/delete` | 0.1.0 |

### Sessions

| Method | Endpoint | Since |
|--------|----------|-------|
| `POST` | `/sessions` | 0.6.0 |
| `GET` | `/sessions` | 0.6.0 |
| `PATCH` | `/sessions/{id}` | 0.6.0 |
| `GET` | `/sessions/{id}/attribution` | 0.8.0 |
| `POST` | `/sessions/cleanup` | 0.8.0 |

### Health & Usage

| Method | Endpoint | Since |
|--------|----------|-------|
| `GET` | `/health` | 0.5.0 |
| `GET` | `/ready` | 0.5.0 |
| `GET` | `/health/detailed` | 0.8.0 |
| `GET` | `/me/usage` | 0.8.0 |
| `GET` | `/metrics` | 0.6.1 |

### Knowledge

| Method | Endpoint | Since |
|--------|----------|-------|
| `POST` | `/promote` | 0.6.0 |
| `POST` | `/cognify` | 0.6.0 |
| `POST` | `/lint` | 0.6.0 |

### Documentation

| Method | Endpoint | Since |
|--------|----------|-------|
| `GET` | `/docs` | 0.8.0 |
| `GET` | `/redoc` | 0.8.0 |
| `GET` | `/openapi.json` | 0.8.0 |
