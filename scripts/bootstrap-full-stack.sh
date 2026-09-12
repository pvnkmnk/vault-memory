#!/usr/bin/env bash
# bootstrap-full-stack.sh — one-shot local full-stack setup for vault-memory.
#
# Runs entirely on YOUR machine under Docker Desktop (no cloud, no credit
# card). Brings up: Postgres 16 + Weaviate + Ollama (with a real model), then
# the vault-memory daemon against them, and (optionally) serves the daemon to
# your tailnet over Tailscale Funnel so other agents/boxes can reach it.
#
# Usage:
#   sh scripts/bootstrap-full-stack.sh up        # start everything
#   sh scripts/bootstrap-full-stack.sh model     # pull a bigger Ollama model
#   sh scripts/bootstrap-full-stack.sh funnel    # expose daemon via Tailscale
#   sh scripts/bootstrap-full-stack.sh status    # what's running
#   sh scripts/bootstrap-full-stack.sh down      # stop (data volumes kept)
#
# Requirements: Docker Desktop; Tailscale CLI only for `funnel`.
# POSIX sh compatible (dash/Debian sh included): no pipefail, no bashisms.
#
# Running on a cloud VM instead of your own machine? See
# scripts/cloud-init-full-stack.yaml — the same stack provisioned from
# cloud-init user-data (Docker + Tailscale + daemon as a systemd service).
set -eu

# Default model: 7B class, quality jump over the CI 0.5B/1B models.
# Override: MODEL=llama3.1:8b-instruct-q4_K_M sh scripts/bootstrap-full-stack.sh model
MODEL="${MODEL:-qwen2.5:7b-instruct-q4_K_M}"

DC="docker compose"

log()  { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
ok()   { printf '\033[1;32m ✓\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m !\033[0m %s\n' "$*"; }

need() { command -v "$1" >/dev/null 2>&1 || { warn "$1 is required but not installed"; exit 1; }; }

_wait_ollama_ready() {
  log "Waiting for Ollama health…"
  for i in $(seq 1 30); do
    $DC exec -T ollama ollama list >/dev/null 2>&1 && return 0
    sleep 2
  done
  warn "Timed out waiting for Ollama to become ready"
  return 1
}

cmd_up() {
  need docker
  log "Starting base stack (Postgres + Weaviate)…"
  $DC up -d weaviate postgres
  $DC up -d --wait weaviate postgres || warn "waiting for healthchecks…"
  ok "Postgres on 127.0.0.1:5432 (vault/vault_local) — Weaviate on 127.0.0.1:8080"

  log "Starting Ollama (profile: llm)…"
  $DC --profile llm up -d ollama
  _wait_ollama_ready
  ok "Ollama on http://127.0.0.1:11434"

  log "Pulling model: $MODEL (this is the big download, one time)…"
  $DC exec -T ollama ollama pull "$MODEL"
  ok "Model $MODEL resident (OLLAMA_KEEP_ALIVE=10m between /cognify calls)"

  log "Initializing schema (init_db.sql)…"
  docker exec -i vault-memory-postgres psql -U vault -d vault_memory \
    < init_db.sql >/dev/null 2>&1 || ok "schema already applied (init_db ran on first boot)"

  log "Daemon: install the package, then run it against the stack:"
  cat <<EOF

    # one-time (Python 3.11+ venv):
    python3 -m venv .venv && source .venv/bin/activate
    pip install -e .

    # daemon with the local LLM for /cognify:
    export LLM_PROVIDER=ollama
    export OLLAMA_URL=http://127.0.0.1:11434
    export OLLAMA_MODEL=$MODEL
    export PG_CONNECTION_STRING='dbname=vault_memory user=vault password=vault_local host=localhost'
    vault-memory daemon start          # or: uvicorn daemon.main:app --port 5051

    # sync a vault + smoke-test:
    vault-memory sync --full --vault /path/to/vault
    curl -s localhost:5051/health/detailed | head -40
EOF
}

cmd_model() {
  need docker
  log "Pulling $MODEL into the ollama container…"
  $DC --profile llm up -d ollama
  _wait_ollama_ready
  $DC exec -T ollama ollama pull "$MODEL"
  ok "Available models:"
  $DC exec -T ollama ollama list
}

cmd_funnel() {
  need tailscale
  log "Exposing the daemon to your tailnet via Tailscale Serve…"
  # Private to your tailnet (machines logged into your tailnet only).
  tailscale serve --bg --yes 5051
  ok "Tailnet URL: https://$(tailscale status --json | sed -n 's/.*"DNSName": "\([^"]*\).*/\1/p' | head -1 | sed 's/\.$//')"
  cat <<'EOF'

  Tailnet-private (recommended): agents on your other machines set
    VAULT_MEMORY_URL=https://<your-machine>.<tailnet>.ts.net
  and reach the daemon from anywhere, encrypted, no ports open to the internet.

  Public internet instead?  tailscale funnel 5051   (adds a public URL;
  set VAULT_MEMORY_API_KEY first — a keyless daemon should not be public.)
EOF
}

cmd_status() {
  $DC ps
  echo
  if command -v tailscale >/dev/null 2>&1 && tailscale status >/dev/null 2>&1; then
    log "Tailscale serve status:"
    tailscale serve status || true
  fi
}

cmd_down() {
  log "Stopping stack (volumes kept — data survives)…"
  $DC --profile llm down
  tailscale serve --https=443 off 2>/dev/null || tailscale serve off 2>/dev/null || true
  ok "Stopped. 'up' again later reuses the same data."
}

case "${1:-up}" in
  up)     cmd_up ;;
  model)  cmd_model ;;
  funnel) cmd_funnel ;;
  status) cmd_status ;;
  down)   cmd_down ;;
  *) echo "usage: $0 {up|model|funnel|status|down}"; exit 1 ;;
esac
