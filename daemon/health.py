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

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter

# ---------------------------------------------------------------------------
# Health router for daemon health endpoints
# ---------------------------------------------------------------------------

router = APIRouter(tags=["health"])

# Dependency status tracking
_dependency_status = {
    "weaviate": {"status": "unknown", "last_check": None, "latency_ms": None},
    "postgres": {"status": "unknown", "last_check": None, "latency_ms": None},
    "embedder": {"status": "unknown", "last_check": None, "latency_ms": None},
}


def update_dependency_status(name: str, status: str, latency_ms: Optional[float] = None):
    """Update dependency health status."""
    _dependency_status[name] = {
        "status": status,
        "last_check": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "latency_ms": round(latency_ms, 2) if latency_ms else None,
    }


@router.get("/health")
async def health():
    """Liveness probe — is the daemon running?"""
    return {
        "status": get_daemon_state().get("status", "unknown"),
        "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "degraded": get_daemon_state().get("degraded", False),
    }


@router.get("/ready")
async def ready():
    """Readiness probe — is the daemon ready to serve requests?"""

    # Check if any critical dependencies are down
    critical_deps = ["weaviate", "postgres"]
    failed_deps = [
        name
        for name in critical_deps
        if _dependency_status.get(name, {}).get("status") != "healthy"
    ]

    if failed_deps:
        return {
            "status": "not_ready",
            "reason": f"Dependencies not healthy: {failed_deps}",
            "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "dependencies": _dependency_status,
        }

    return {
        "status": "ready",
        "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "dependencies": _dependency_status,
    }


# ---------------------------------------------------------------------------
# Prometheus Metrics Endpoint
# ---------------------------------------------------------------------------

# Simple in-memory metrics storage
_metrics = {
    "requests_total": 0,
    "requests_by_endpoint": {},
    "request_duration_seconds": [],
    "errors_total": 0,
    "errors_by_code": {},
    "active_sessions": 0,
    "last_reset": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
}


def increment_request_count(endpoint: str, status_code: int, duration_ms: float):
    """Record a request metric."""
    _metrics["requests_total"] += 1

    if endpoint not in _metrics["requests_by_endpoint"]:
        _metrics["requests_by_endpoint"][endpoint] = {"count": 0, "errors": 0}
    _metrics["requests_by_endpoint"][endpoint]["count"] += 1

    if status_code >= 400:
        _metrics["requests_by_endpoint"][endpoint]["errors"] += 1
        _metrics["errors_total"] += 1
        error_code = f"{status_code // 100}xx"
        _metrics["errors_by_code"][error_code] = _metrics["errors_by_code"].get(error_code, 0) + 1

    _metrics["request_duration_seconds"].append(duration_ms / 1000)
    # Keep only last 1000 durations for memory efficiency
    if len(_metrics["request_duration_seconds"]) > 1000:
        _metrics["request_duration_seconds"] = _metrics["request_duration_seconds"][-1000:]


def set_active_sessions(count: int):
    """Update active session count."""
    _metrics["active_sessions"] = count


@router.get("/metrics")
async def metrics():
    """Prometheus-compatible metrics endpoint."""
    lines = []

    # Daemon info
    lines.append("# HELP vault_memory_daemon_info Daemon information")
    lines.append("# TYPE vault_memory_daemon_info gauge")
    lines.append(
        f'vault_memory_daemon_info{{version="0.5.0",status="{get_daemon_state().get("status", "unknown")}"}} 1'
    )

    # Total requests
    lines.append("# HELP vault_memory_requests_total Total HTTP requests")
    lines.append("# TYPE vault_memory_requests_total counter")
    lines.append(f"vault_memory_requests_total {_metrics['requests_total']}")

    # Requests by endpoint
    lines.append("# HELP vault_memory_requests_by_endpoint Requests by endpoint")
    lines.append("# TYPE vault_memory_requests_by_endpoint counter")
    for endpoint, data in _metrics["requests_by_endpoint"].items():
        safe_endpoint = endpoint.replace('"', '\\"')
        lines.append(
            f'vault_memory_requests_by_endpoint{{endpoint="{safe_endpoint}"}} {data["count"]}'
        )

    # Error rate
    lines.append("# HELP vault_memory_errors_total Total errors")
    lines.append("# TYPE vault_memory_errors_total counter")
    lines.append(f"vault_memory_errors_total {_metrics['errors_total']}")

    # Errors by code
    lines.append("# HELP vault_memory_errors_by_code Errors by HTTP status code family")
    lines.append("# TYPE vault_memory_errors_by_code counter")
    for code, count in _metrics["errors_by_code"].items():
        lines.append(f'vault_memory_errors_by_code{{code="{code}"}} {count}')

    # Request duration histogram (simplified)
    if _metrics["request_duration_seconds"]:
        durations = _metrics["request_duration_seconds"]
        lines.append("# HELP vault_memory_request_duration_seconds Request duration")
        lines.append("# TYPE vault_memory_request_duration_seconds histogram")
        lines.append(f"vault_memory_request_duration_seconds_count {len(durations)}")
        lines.append(f"vault_memory_request_duration_seconds_sum {sum(durations)}")
        avg = sum(durations) / len(durations)
        lines.append(f"vault_memory_request_duration_seconds_avg {avg}")

    # Active sessions
    lines.append("# HELP vault_memory_active_sessions Active agent sessions")
    lines.append("# TYPE vault_memory_active_sessions gauge")
    lines.append(f"vault_memory_active_sessions {_metrics['active_sessions']}")

    # Dependency health
    lines.append("# HELP vault_memory_dependency_health Dependency health status")
    lines.append("# TYPE vault_memory_dependency_health gauge")
    for dep_name, dep_data in _dependency_status.items():
        healthy = 1 if dep_data.get("status") == "healthy" else 0
        lines.append(f'vault_memory_dependency_health{{name="{dep_name}"}} {healthy}')

    # Daemon state
    lines.append("# HELP vault_memory_daemon_degraded Daemon degraded status")
    lines.append("# TYPE vault_memory_daemon_degraded gauge")
    degraded = 1 if get_daemon_state().get("degraded") else 0
    lines.append(f"vault_memory_daemon_degraded {degraded}")

    return "\n".join(lines)


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
    _daemon_state["indexing_started"] = (
        datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    )


def record_index_complete(stats: Optional[Dict[str, Any]] = None):
    """Record index completion and update daemon state.

    Args:
        stats: Optional dict with indexing statistics (files_processed, chunks_upserted, etc.)
    """
    _daemon_state["status"] = "ready"
    _daemon_state["last_index_complete"] = (
        datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    )
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
