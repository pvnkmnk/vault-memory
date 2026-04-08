"""
Daemon health probe + agent runtime directory detection.

Agent runtime dirs (P4):
  .agents/    generic agent config  (AGENTS.md, skills)
  .gemini/    Gemini CLI system prompt + settings
  .goose/     Goose toolkit config
  .opencode/  OpenCode agent config

INJECTION CONTRACT:
  When vault_root is supplied to `detect_agent_runtimes()`, the function
  reads any AGENTS.md found and returns it as a high-priority memory block
  suitable for prepending to the session context.
"""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter

# ---------------------------------------------------------------------------
# Health router for daemon health endpoints
# ---------------------------------------------------------------------------

router = APIRouter(tags=["health"])


@router.get("/health")
async def health():
    """Liveness probe — is the daemon running?"""
    return {
        "status": "ok",
        "timestamp": datetime.utcnow().isoformat() + "Z",
    }


@router.get("/ready")
async def ready():
    """Readiness probe — is the daemon ready to serve requests?"""
    return {
        "status": "ready",
        "timestamp": datetime.utcnow().isoformat() + "Z",
    }


# ---------------------------------------------------------------------------
# Daemon lifecycle state management
# ---------------------------------------------------------------------------

_daemon_state = {"status": "starting", "degraded": False}


def mark_ready():
    """Mark the daemon as ready to serve requests."""
    _daemon_state["status"] = "ready"
    _daemon_state["degraded"] = False


def mark_degraded(reason: Optional[str] = None):
    """Mark the daemon as degraded (still running but limited functionality."""
    _daemon_state["status"] = "degraded"
    _daemon_state["degraded"] = True
    if reason:
        _daemon_state["reason"] = reason


def get_daemon_state() -> Dict[str, Any]:
    """Get current daemon state."""
    return dict(_daemon_state)


def mark_indexing():
    """Mark the daemon as currently indexing (sync in progress)."""
    _daemon_state["status"] = "indexing"
    _daemon_state["indexing_started"] = datetime.utcnow().isoformat() + "Z"


def record_index_complete(stats: Optional[Dict[str, Any]] = None):
    """Record index completion and update daemon state.

    Args:
        stats: Optional dict with indexing statistics (files_processed, chunks_upserted, etc.)
    """
    _daemon_state["status"] = "ready"
    _daemon_state["last_index_complete"] = datetime.utcnow().isoformat() + "Z"
    if stats:
        _daemon_state["last_index_stats"] = stats


# ---------------------------------------------------------------------------
# Agent runtime directory registry
# ---------------------------------------------------------------------------

AGENT_RUNTIME_DIRS: Dict[str, Dict[str, Any]] = {
    ".agents": {
        "label": "generic-agent",
        "description": "Generic agent config (AGENTS.md, skills)",
        "priority": "high",
        "read_files": ["AGENTS.md"],
    },
    ".gemini": {
        "label": "gemini-cli",
        "description": "Gemini CLI system prompt + settings",
        "priority": "high",
        "read_files": ["system-prompt.md", "settings.json"],
    },
    ".goose": {
        "label": "goose",
        "description": "Goose toolkit config",
        "priority": "medium",
        "read_files": ["config.yaml", "config.yml"],
    },
    ".opencode": {
        "label": "opencode",
        "description": "OpenCode agent config",
        "priority": "medium",
        "read_files": ["config.json", "AGENTS.md"],
    },
}


def detect_agent_runtimes(
    vault_root: Optional[str] = None,
    cwd: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Scan for agent runtime config directories in:
      1. vault_root (if provided)
      2. cwd (current working directory) — for CLI agent invocations
      3. user home directory

    Returns a dict with:
      - detected: list of detected runtime entries
      - agents_md: contents of AGENTS.md if found (high-priority injection)
      - token_estimate: rough token count of agents_md
    """
    search_roots: List[Path] = []
    if vault_root:
        search_roots.append(Path(vault_root).expanduser().resolve())
    if cwd:
        search_roots.append(Path(cwd).expanduser().resolve())
    home = Path.home()
    if home not in search_roots:
        search_roots.append(home)

    detected: List[Dict[str, Any]] = []
    agents_md_content: Optional[str] = None

    for root in search_roots:
        for dir_name, meta in AGENT_RUNTIME_DIRS.items():
            dir_path = root / dir_name
            if not dir_path.is_dir():
                continue

            entry: Dict[str, Any] = {
                "dir": dir_name,
                "label": meta["label"],
                "description": meta["description"],
                "priority": meta["priority"],
                "found_at": str(dir_path),
                "files_found": [],
                "files_content": {},
            }

            for fname in meta["read_files"]:
                fpath = dir_path / fname
                if fpath.exists():
                    entry["files_found"].append(fname)
                    try:
                        content = fpath.read_text(encoding="utf-8", errors="replace")
                        entry["files_content"][fname] = content
                        # AGENTS.md from .agents/ is the canonical high-priority block
                        if fname == "AGENTS.md" and agents_md_content is None:
                            agents_md_content = content
                    except OSError:
                        pass

            if entry["files_found"]:
                detected.append(entry)

    token_estimate = max(1, len(agents_md_content) // 4) if agents_md_content else 0

    return {
        "detected": detected,
        "agents_md": agents_md_content,
        "token_estimate": token_estimate,
        "search_roots": [str(r) for r in search_roots],
    }


# ---------------------------------------------------------------------------
# Basic daemon health probe (unchanged API)
# ---------------------------------------------------------------------------


def probe_health(daemon_url: str = "http://localhost:5051") -> Dict[str, Any]:
    """
    Quick HTTP health check against the vault-memoryd daemon.
    Returns a dict suitable for CLI display or MCP health tool.
    """
    import httpx

    result: Dict[str, Any] = {
        "daemon_url": daemon_url,
        "liveness": None,
        "readiness": None,
        "error": None,
    }
    try:
        liveness = httpx.get(f"{daemon_url}/health", timeout=3.0)
        readiness = httpx.get(f"{daemon_url}/ready", timeout=3.0)
        result["liveness"] = liveness.json()
        result["readiness"] = readiness.json()
    except Exception as e:
        result["error"] = str(e)
    return result
