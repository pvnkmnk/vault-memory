# cli/tools/vault.py
"""System-related MCP tools: vault_lint, health."""

import httpx
from typing import Any, Dict

from cli.mcp_client import _auth_headers

TOOLS = [
    {
        "name": "health",
        "description": "Check vault-memoryd daemon status.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "vault_lint",
        "description": "Run vault health checks (orphans, contradictions, stale nodes, missing/unlinked pages) and optionally file lint-YYYY-MM-DD.md.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "vault_path": {"type": "string", "description": "Absolute path to vault root"},
                "stale_days": {
                    "type": "integer",
                    "description": "Days before non-tree content is considered stale",
                    "default": 30,
                },
                "file_report": {
                    "type": "boolean",
                    "description": "Write lint report markdown file to vault root",
                    "default": True,
                },
                "daemon_url": {
                    "type": "string",
                    "description": "Daemon URL (default: http://localhost:5051)",
                    "default": "http://localhost:5051",
                },
            },
            "required": ["vault_path"],
        },
    },
]


def _vault_lint(args: Dict, daemon_url: str) -> Dict:
    daemon_url = args.get("daemon_url", daemon_url)
    payload = {
        "vault_path": args["vault_path"],
        "stale_days": int(args.get("stale_days", 30)),
        "file_report": bool(args.get("file_report", True)),
    }
    try:
        r = httpx.post(f"{daemon_url}/lint", json=payload, timeout=30.0, headers=_auth_headers)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        return {"error": f"vault_lint failed: {e}", "payload_sent": payload}


def get_tools() -> list:
    """Return the vault/system tool definitions."""
    return TOOLS
