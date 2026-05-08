# cli/mcp_adapter.py
"""
MCP stdio adapter.
Translates Model Context Protocol JSON-RPC messages to daemon HTTP calls.
Compatible with Claude Desktop, Cursor, Cline, Gemini CLI, OpenCode, and any MCP-compliant client.

Usage:
  python -m cli.mcp_adapter [--daemon-url URL] [--api-key KEY]
  vault-memory mcp [--daemon-url URL] [--api-key KEY]

Environment variables:
  VAULT_MEMORY_URL      Daemon URL (default: http://127.0.0.1:5051)
  VAULT_MEMORY_API_KEY  API key for daemon authentication

Tools are now defined in cli/tools/ modules:
  - cli/tools/retrieval.py  — search, search_siblings, graph, temporal
  - cli/tools/context.py   — memory/attach_block, memory/list_blocks, memory/read_batch,
                                   memory/write_working, memory/delete_working,
                                   memory/trigger_lookup, memory/project_state
  - cli/tools/sessions.py  — memory/session_register, memory/session_close
  - cli/tools/knowledge.py  — memory/cognify, memory/promote
  - cli/tools/vault.py      — health, vault_lint
"""

import argparse
import json
import logging
import os
import sys

import httpx

from cli.mcp_client import _auth_headers, set_auth_headers, call_daemon
from cli.tools.retrieval import get_tools as get_retrieval_tools
from cli.tools.context import get_tools as get_context_tools
from cli.tools.sessions import get_tools as get_sessions_tools
from cli.tools.knowledge import get_tools as get_knowledge_tools
from cli.tools.vault import get_tools as get_vault_tools

# Backward compatibility: expose functions at module level for tests
from cli.mcp_client import call_daemon as _call_daemon
from cli.tools.knowledge import _memory_cognify as _memory_cognify
from cli.tools.knowledge import _memory_promote as _memory_promote
from cli.tools.vault import _vault_lint as _vault_lint

logger = logging.getLogger("vault-memory.mcp")

# Combined tool list from all modules
TOOLS = []
TOOLS.extend(get_retrieval_tools())
TOOLS.extend(get_context_tools())
TOOLS.extend(get_sessions_tools())
TOOLS.extend(get_knowledge_tools())
TOOLS.extend(get_vault_tools())


# ---------------------------------------------------------------------------
# MCP JSON-RPC stdio loop
# ---------------------------------------------------------------------------

def run_mcp_adapter(daemon_url: str, api_key: str = None):
    global _auth_headers
    # API key: CLI arg > environment variable
    effective_key = api_key or os.getenv("VAULT_MEMORY_API_KEY", "")
    set_auth_headers({"x-api-key": effective_key} if effective_key else {})

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue

        msg_id = msg.get("id")
        method = msg.get("method", "")
        params = msg.get("params", {})

        if method == "initialize":
            _send(
                {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": {
                        "protocolVersion": "2024-11-05",
                        "serverInfo": {"name": "vault-memory", "version": "0.5.0-p3"},
                        "capabilities": {"tools": {}},
                    },
                }
            )

        elif method == "tools/list":
            _send({"jsonrpc": "2.0", "id": msg_id, "result": {"tools": TOOLS}})

        elif method == "tools/call":
            tool_name = params.get("name")
            tool_args = params.get("arguments", {})
            try:
                result = call_daemon(daemon_url, tool_name, tool_args)
                _send(
                    {
                        "jsonrpc": "2.0",
                        "id": msg_id,
                        "result": {
                            "content": [{"type": "text", "text": json.dumps(result, indent=2)}],
                            "isError": False,
                        },
                    }
                )
            except Exception as e:
                _send(
                    {
                        "jsonrpc": "2.0",
                        "id": msg_id,
                        "result": {
                            "content": [{"type": "text", "text": f"Error: {e}"}],
                            "isError": True,
                        },
                    }
                )

        elif method == "notifications/initialized":
            pass

        else:
            _send(
                {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "error": {"code": -32601, "message": f"Method not found: {method}"},
                }
            )


def _send(obj: dict):
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def main():
    parser = argparse.ArgumentParser(
        description="vault-memory MCP stdio adapter",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--daemon-url",
        dest="daemon_url",
        default=os.getenv("VAULT_MEMORY_URL", "http://127.0.0.1:5051"),
        help="Vault-memory daemon URL (default: $VAULT_MEMORY_URL or http://127.0.0.1:5051)",
    )
    parser.add_argument(
        "--api-key",
        dest="api_key",
        default=None,
        help="API key for daemon authentication (overrides VAULT_MEMORY_API_KEY env var)",
    )
    args = parser.parse_args()
    run_mcp_adapter(daemon_url=args.daemon_url, api_key=args.api_key)


if __name__ == "__main__":
    main()


# ---------------------------------------------------------------------------
# Backward-compatible wrappers for tests
# ---------------------------------------------------------------------------

def _call_daemon(daemon_url: str, tool: str, args: dict):
    """Backward-compatible wrapper for tests."""
    from cli.mcp_client import call_daemon as _call
    return _call(daemon_url, tool, args)


def _memory_cognify(args):
    """Backward-compatible wrapper for tests."""
    from cli.tools.knowledge import _memory_cognify as _func
    return _func(args, args.get("daemon_url", "http://localhost:5051"))


def _memory_promote(args):
    """Backward-compatible wrapper for tests."""
    from cli.tools.knowledge import _memory_promote as _func
    return _func(args, args.get("daemon_url", "http://localhost:5051"))


def _vault_lint(args):
    """Backward-compatible wrapper for tests."""
    from cli.tools.vault import _vault_lint as _func
    return _func(args, args.get("daemon_url", "http://localhost:5051"))


# Backward-compatible imports for tests
from cli.mcp_client import _auth_headers
from cli.tools.context import (
    _memory_attach_block,
    _memory_list_blocks,
    _memory_read_batch,
    _memory_write_working,
    _memory_delete_working,
    _memory_trigger_lookup,
    _memory_project_state,
)
from cli.tools.sessions import _memory_session_register, _memory_session_close
