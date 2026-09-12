#!/bin/sh
# .devcontainer/post-create.sh — one-time Codespaces setup.
#
# Runs after the container is built (not on every start; see post-start.sh).
# Installs the Python package into a repo-local venv and lays down a small
# sample vault so `vault-memory search` has something to find immediately.
set -eu

REPO_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$REPO_ROOT"

VAULT_PATH="${VAULT_PATH:-/workspaces/sample-vault}"

log() { printf '\033[1;36m==> %s\033[0m\n' "$*"; }
ok()  { printf '\033[1;32m ✓ %s\033[0m\n' "$*"; }

# ── 1. Python package ────────────────────────────────────────────────────────
# Note: pyproject.toml defines no [project.scripts] entry points, so this does
# NOT install a `vault-memory` binary — the daemon is started as
# `python -m daemon.main` / `uvicorn daemon.main:app`. See post-start.sh.
if [ ! -x ".venv/bin/python" ]; then
  log "Creating .venv"
  python3 -m venv .venv
fi

log "Installing vault-memory (first run pulls torch/transformers — several minutes)"
.venv/bin/python -m pip install --quiet --upgrade pip
# --no-cache-dir keeps the Codespaces disk (32 GB free tier) from carrying a
# second copy of every wheel in ~/.cache/pip.
.venv/bin/python -m pip install --quiet --no-cache-dir -e .
ok "Installed; import check:"
.venv/bin/python -c "import daemon.version as v; print('   vault-memory', v.__version__)"

# ── 2. Sample vault ──────────────────────────────────────────────────────────
log "Creating a sample vault at $VAULT_PATH"
mkdir -p "$VAULT_PATH/_working" "$VAULT_PATH/Projects/demo"

if [ ! -f "$VAULT_PATH/README.md" ]; then
  cat > "$VAULT_PATH/README.md" <<'EOF'
---
title: Sample Vault
tags: [demo, vault-memory]
importance: 0.9
trust: high
maturity: sapling
---
# Sample Vault

This vault exists so a fresh Codespace has something to index. It was created
by `.devcontainer/post-create.sh`.

Delete the `/workspaces/sample-vault` directory and point `VAULT_PATH` at a
real vault clone instead.
EOF

  cat > "$VAULT_PATH/Projects/demo/architecture.md" <<'EOF'
---
title: Demo Architecture
tags: [demo, architecture]
project: demo
importance: 0.8
maturity: sapling
---
# Demo Architecture

The stack is Postgres for temporal/graph metadata and Weaviate for vectors.
Retrieval fuses dense + sparse + graph + temporal via reciprocal rank fusion.
EOF

  cat > "$VAULT_PATH/Projects/demo/roadmap.md" <<'EOF'
---
title: Demo Roadmap
tags: [demo, roadmap]
project: demo
importance: 0.5
maturity: seed
---
# Demo Roadmap

- [x] Bring the stack up in a Codespace
- [ ] Sync this sample vault
- [ ] Run a first search
EOF
fi
ok "Sample vault ready"

log "Done. The stack (Postgres + Weaviate + Ollama) and the daemon start"
log "automatically via .devcontainer/post-start.sh on every container start."
log "Re-run it by hand any time: sh .devcontainer/post-start.sh"
