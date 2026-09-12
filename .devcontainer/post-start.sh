#!/bin/sh
# .devcontainer/post-start.sh — bring the stack up (idempotent).
#
# Runs on every Codespaces start/resume, and is safe to re-run by hand:
#   sh .devcontainer/post-start.sh
#
# Starts Postgres + Weaviate + Ollama, applies the schema, then launches the
# FastAPI daemon in the background on the forwarded port.
set -eu

REPO_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$REPO_ROOT"

PORT="${VAULT_MEMORY_PORT:-5051}"
MODEL="${OLLAMA_MODEL:-llama3.2:1b}"
LOG_FILE=/tmp/vault-memory-daemon.log

log()  { printf '\033[1;36m==> %s\033[0m\n' "$*"; }
ok()   { printf '\033[1;32m ✓ %s\033[0m\n' "$*"; }
warn() { printf '\033[1;33m ! %s\033[0m\n' "$*"; }

# ── 1. Docker daemon ─────────────────────────────────────────────────────────
log "Waiting for the Docker daemon (Docker-in-Docker)…"
i=0
while [ "$i" -lt 45 ]; do
  docker info >/dev/null 2>&1 && break
  i=$((i + 1))
  sleep 2
done
if ! docker info >/dev/null 2>&1; then
  warn "Docker daemon never came up — rebuild the container (Dev Containers: Rebuild)"
  exit 1
fi
ok "Docker ready"

# ── 2. Data services ─────────────────────────────────────────────────────────
log "Starting Postgres + Weaviate"
docker compose up -d --wait weaviate postgres || warn "healthchecks still settling"
ok "Postgres 127.0.0.1:5432 · Weaviate 127.0.0.1:8080"

log "Starting Ollama"
docker compose --profile llm up -d ollama

i=0
while [ "$i" -lt 45 ]; do
  docker compose exec -T ollama ollama list >/dev/null 2>&1 && break
  i=$((i + 1))
  sleep 2
done
if docker compose exec -T ollama ollama list >/dev/null 2>&1; then
  if docker compose exec -T ollama ollama list | grep -q "$MODEL"; then
    ok "Model $MODEL already present"
  else
    log "Pulling $MODEL (one-time, ~1 GB)"
    docker compose exec -T ollama ollama pull "$MODEL"
    ok "Model $MODEL resident"
  fi
else
  warn "Ollama did not become ready; /cognify will be unavailable"
fi

# ── 3. Schema ────────────────────────────────────────────────────────────────
log "Applying init_db.sql (no-op after the first run)"
docker exec -i vault-memory-postgres psql -U vault -d vault_memory \
  < init_db.sql >/dev/null 2>&1 || ok "schema already present"

# ── 4. Daemon ────────────────────────────────────────────────────────────────
# Bound to 0.0.0.0 so Codespaces forwards 5051 reliably. The forwarded port is
# private to your account by default; set VAULT_MEMORY_API_KEY before making it
# public.
if [ ! -x ".venv/bin/python" ]; then
  warn "No .venv — post-create did not finish. Run: sh .devcontainer/post-create.sh"
  exit 1
fi

if pgrep -f "uvicorn daemon.main:app" >/dev/null 2>&1; then
  ok "Daemon already running"
else
  log "Starting the daemon on port $PORT (log: $LOG_FILE)"
  nohup .venv/bin/python -m uvicorn daemon.main:app \
    --host 0.0.0.0 --port "$PORT" >"$LOG_FILE" 2>&1 &
  sleep 3
fi

i=0
while [ "$i" -lt 60 ]; do
  if curl -fsS "http://127.0.0.1:$PORT/health" >/dev/null 2>&1; then break; fi
  i=$((i + 1))
  sleep 2
done

# ── 5. Report ────────────────────────────────────────────────────────────────
if curl -fsS "http://127.0.0.1:$PORT/health" >/dev/null 2>&1; then
  ok "Daemon healthy on 127.0.0.1:$PORT"
else
  warn "Daemon not responding yet — tail $LOG_FILE"
fi

if [ -n "${CODESPACE_NAME:-}" ] && [ -n "${GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN:-}" ]; then
  printf '\n   Forwarded URL: https://%s-%s.%s\n' \
    "$CODESPACE_NAME" "$PORT" "$GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN"
fi

cat <<EOF

   Sync the sample vault:
     .venv/bin/python -m cli.main sync --full --vault "\$VAULT_PATH"

   Search:
     .venv/bin/python -m cli.main search -q "architecture"

   Daemon log:    tail -f $LOG_FILE
   Stack logs:    docker compose logs -f
   Stop the stack: docker compose --profile llm down
EOF
