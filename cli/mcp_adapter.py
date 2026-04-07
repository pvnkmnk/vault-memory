# cli/mcp_adapter.py
"""
MCP stdio adapter.
Translates Model Context Protocol JSON-RPC messages to daemon HTTP calls.
Compatible with Claude Desktop, Cursor, Cline, and any MCP-compliant client.
"""

import json
import logging
import sys
from typing import Any, Dict

import httpx

logger = logging.getLogger("vault-memory.mcp")

TOOLS = [
    {
        "name": "search",
        "description": "Search your Obsidian vault using semantic, keyword, graph, and temporal strategies.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query":            {"type": "string",  "description": "Natural language search query"},
                "project":          {"type": "string",  "description": "Optional: scope to a project folder"},
                "top_k":            {"type": "integer", "description": "Number of results (default 5)", "default": 5},
                "include_graph":    {"type": "boolean", "description": "Enable graph traversal strategy"},
                "include_temporal": {"type": "boolean", "description": "Enable temporal history strategy"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "graph",
        "description": "Traverse entity relationships in the knowledge graph.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "entity": {"type": "string", "description": "Entity name to traverse from"},
                "relationship": {"type": "string", "description": "Optional: filter by relationship type"},
            },
            "required": ["entity"],
        },
    },
    {
        "name": "temporal",
        "description": "Query note history within a date range.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "entity": {"type": "string"},
                "start":  {"type": "string", "description": "Start date YYYY-MM-DD"},
                "end":    {"type": "string", "description": "End date YYYY-MM-DD"},
            },
            "required": ["entity"],
        },
    },
    {
        "name": "health",
        "description": "Check vault-memoryd daemon status.",
        "inputSchema": {"type": "object", "properties": {}},
    },
]


def _send(obj: Dict[str, Any]):
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def _call_daemon(daemon_url: str, tool: str, args: Dict) -> Any:
    if tool == "search":
        r = httpx.post(f"{daemon_url}/search", json=args, timeout=30.0)
        return r.json()
    elif tool == "graph":
        r = httpx.get(f"{daemon_url}/graph", params=args, timeout=10.0)
        return r.json()
    elif tool == "temporal":
        r = httpx.get(f"{daemon_url}/temporal", params=args, timeout=10.0)
        return r.json()
    elif tool == "health":
        liveness  = httpx.get(f"{daemon_url}/health",  timeout=3.0).json()
        readiness = httpx.get(f"{daemon_url}/ready",   timeout=3.0).json()
        return {"liveness": liveness, "readiness": readiness}
    else:
        raise ValueError(f"Unknown tool: {tool}")


def run_mcp_adapter(daemon_url: str):
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue

        msg_id  = msg.get("id")
        method  = msg.get("method", "")
        params  = msg.get("params", {})

        if method == "initialize":
            _send({"jsonrpc": "2.0", "id": msg_id, "result": {
                "protocolVersion": "2024-11-05",
                "serverInfo": {"name": "vault-memory", "version": "0.1.0"},
                "capabilities": {"tools": {}},
            }})

        elif method == "tools/list":
            _send({"jsonrpc": "2.0", "id": msg_id, "result": {"tools": TOOLS}})

        elif method == "tools/call":
            tool_name = params.get("name")
            tool_args = params.get("arguments", {})
            try:
                result = _call_daemon(daemon_url, tool_name, tool_args)
                _send({"jsonrpc": "2.0", "id": msg_id, "result": {
                    "content": [{"type": "text", "text": json.dumps(result, indent=2)}],
                    "isError": False,
                }})
            except Exception as e:
                _send({"jsonrpc": "2.0", "id": msg_id, "result": {
                    "content": [{"type": "text", "text": f"Error: {e}"}],
                    "isError": True,
                }})

        elif method == "notifications/initialized":
            pass  # No response needed

        else:
            _send({"jsonrpc": "2.0", "id": msg_id,
                   "error": {"code": -32601, "message": f"Method not found: {method}"}})
