# cli/mcp_client.py
"""HTTP client for making calls to the vault-memory daemon."""

import logging
import httpx
import re
from typing import Any, Dict, List, Optional
from pathlib import Path

logger = logging.getLogger("vault-memory.mcp.client")

# Global auth headers (set by mcp_adapter)
_auth_headers: Dict[str, str] = {}

def set_auth_headers(headers: Dict[str, str]) -> None:
    """Set the global auth headers for daemon calls."""
    global _auth_headers
    _auth_headers = headers


def _sanitize_vault_relative_path(path_str: str, vault_root: Path) -> Optional[Path]:
    """
    Resolve a vault-relative path and confirm it stays within vault_root.
    Returns resolved absolute Path or None if traversal detected.
    """
    path_str = path_str.lstrip("/")
    candidate = (vault_root / path_str).resolve()
    try:
        candidate.relative_to(vault_root.resolve())
        return candidate
    except ValueError:
        return None


def _token_est(text: str) -> int:
    return max(1, len(text) // 4)


def call_daemon(daemon_url: str, tool: str, args: Dict) -> Any:
    """
    Dispatch a tool call to the vault-memory daemon HTTP API.

    Args:
        daemon_url: Base URL of the daemon (e.g., http://localhost:5051)
        tool: Tool name (e.g., "search", "memory/promote")
        args: Tool arguments as a dictionary

    Returns:
        Parsed JSON response from the daemon.

    Raises:
        ValueError: If the tool name is unknown
    """
    if tool == "search":
        r = httpx.post(f"{daemon_url}/search", json=args, timeout=30.0, headers=_auth_headers)
        return r.json()
    elif tool == "search_siblings":
        return _search_siblings(daemon_url, args)
    elif tool == "graph":
        r = httpx.get(f"{daemon_url}/graph", params=args, timeout=10.0, headers=_auth_headers)
        return r.json()
    elif tool == "temporal":
        r = httpx.get(f"{daemon_url}/temporal", params=args, timeout=10.0, headers=_auth_headers)
        return r.json()
    elif tool == "health":
        liveness = httpx.get(f"{daemon_url}/health", timeout=3.0).json()
        readiness = httpx.get(f"{daemon_url}/ready", timeout=3.0).json()
        return {"liveness": liveness, "readiness": readiness}
    elif tool == "memory/attach_block":
        from cli.tools.context import _memory_attach_block
        return _memory_attach_block(args)
    elif tool == "memory/list_blocks":
        from cli.tools.context import _memory_list_blocks
        return _memory_list_blocks()
    elif tool == "memory/read_batch":
        from cli.tools.context import _memory_read_batch
        return _memory_read_batch(args)
    elif tool == "memory/write_working":
        from cli.tools.context import _memory_write_working
        return _memory_write_working(args)
    elif tool == "memory/delete_working":
        from cli.tools.context import _memory_delete_working
        return _memory_delete_working(args)
    elif tool == "memory/trigger_lookup":
        from cli.tools.context import _memory_trigger_lookup
        return _memory_trigger_lookup(args)
    elif tool == "memory/project_state":
        from cli.tools.context import _memory_project_state
        return _memory_project_state(args, daemon_url)
    elif tool == "memory/session_register":
        from cli.tools.sessions import _memory_session_register
        return _memory_session_register(args, daemon_url)
    elif tool == "memory/session_close":
        from cli.tools.sessions import _memory_session_close
        return _memory_session_close(args, daemon_url)
    elif tool == "memory/cognify":
        from cli.tools.knowledge import _memory_cognify
        return _memory_cognify(args, daemon_url)
    elif tool == "memory/promote":
        from cli.tools.knowledge import _memory_promote
        return _memory_promote(args, daemon_url)
    elif tool == "vault_lint":
        from cli.tools.vault import _vault_lint
        return _vault_lint(args, daemon_url)
    else:
        raise ValueError(f"Unknown tool: {tool}")


# --- search_siblings helper (needs daemon call + fallback) ---

def _search_siblings(daemon_url: str, args: Dict) -> Dict:
    """Hub-based topic sibling discovery with semantic fallback."""
    from pathlib import Path as Path2
    import re

    limit = args.get("limit", 10)
    seed_path = args["seed_path"]
    vault_path = args["vault_path"]
    vault_root = Path(vault_path)

    seed_file = _sanitize_vault_relative_path(seed_path, vault_root)
    if seed_file is None or not seed_file.exists():
        return {
            "error": f"Seed note not found: {seed_path}",
            "seed_path": seed_path,
            "siblings": [],
        }

    seed_content = seed_file.read_text(encoding="utf-8")
    seed_hubs = set()
    hub_pattern = r"#topic:([\w-]+)"
    for m in re.finditer(hub_pattern, seed_content):
        seed_hubs.add(m.group(1))
    if not seed_hubs:
        hub_pattern2 = r"^tags\s*:\s*(\[.*\])"
        m = re.search(hub_pattern2, seed_content, re.MULTILINE)
        if m:
            tags = re.findall(r"[\w-]+", m.group(1))
            seed_hubs = set(tags)
    if not seed_hubs:
        return {
            "seed_path": seed_path,
            "entity": Path(seed_path).stem,
            "limit": limit,
            "siblings": [],
            "note": "No topic hubs or tags found in seed note.",
        }
    try:
        r = httpx.post(
            f"{daemon_url}/search_siblings",
            json={"query": Path(seed_path).stem, "top_k": limit},
            timeout=15.0,
            headers=_auth_headers,
        )
        r.raise_for_status()
        siblings = r.json().get("siblings", [])
        return {
            "seed_path": seed_path,
            "entity": Path(seed_path).stem,
            "limit": limit,
            "siblings": siblings,
            "hubs_used": list(seed_hubs),
        }
    except Exception as e:
        logger.warning("siblings lookup failed: %s", e)
        try:
            r = httpx.post(
                f"{daemon_url}/search",
                json={"query": seed_content[:1000], "project": "", "top_k": limit},
                timeout=15.0,
                headers=_auth_headers,
            )
            r.raise_for_status()
            fallback_results = r.json().get("results", [])
            siblings = []
            for item in fallback_results[:limit]:
                path = item.get("path", "") or item.get("vault_path", "")
                if path != seed_path:
                    siblings.append(
                        {
                            "path": path,
                            "title": item.get("title", ""),
                            "score": item.get("score", 0.0),
                            "shared_hub": None,
                        }
                    )
            return {
                "seed_path": seed_path,
                "entity": Path(seed_path).stem,
                "limit": limit,
                "siblings": siblings,
                "hubs_used": list(seed_hubs),
                "note": "Fallback used: /search_siblings unavailable; results from semantic /search.",
                "fallback_used": True,
            }
        except Exception as e2:
            logger.warning("siblings fallback failed: %s", e2)
            return {
                "seed_path": seed_path,
                "entity": Path(seed_path).stem,
                "limit": limit,
                "siblings": [],
                "hubs_used": list(seed_hubs),
                "error": f"Sibling discovery failed: {e2}",
                "fallback_used": False,
            }
