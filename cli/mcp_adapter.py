# cli/mcp_adapter.py
"""
MCP stdio adapter.
Translates Model Context Protocol JSON-RPC messages to daemon HTTP calls.
Compatible with Claude Desktop, Cursor, Cline, Gemini CLI, OpenCode, and any MCP-compliant client.

Tools (v0.2.0):
  search              — 4-strategy vault search
  graph               — entity relationship traversal
  temporal            — date-range history query
  health              — daemon status
  memory/attach_block — attach named context block to session
  memory/list_blocks  — list attached blocks + token counts
  memory/write_working — write note to _working/ buffer (agent-safe)
  memory/trigger_lookup — keyword → context block mapping
"""

import json
import logging
import os
import sys
from pathlib import Path
from typing import Any, Dict, List

import httpx

logger = logging.getLogger("vault-memory.mcp")

# In-process session state for attached blocks
_attached_blocks: List[Dict[str, Any]] = []

TOOLS = [
    {
        "name": "search",
        "description": "Search your Obsidian vault using semantic, keyword, graph, and temporal strategies. Returns ranked results with trust and agent_written flags.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query":            {"type": "string",  "description": "Natural language search query"},
                "project":          {"type": "string",  "description": "Optional: scope to a project folder"},
                "top_k":            {"type": "integer", "description": "Number of results (default 5)", "default": 5},
                "include_graph":    {"type": "boolean", "description": "Enable graph traversal strategy"},
                "include_temporal": {"type": "boolean", "description": "Enable temporal history strategy"},
                "apply_decay":      {"type": "boolean", "description": "Apply temporal decay scoring (default true)", "default": True},
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
                "entity":       {"type": "string", "description": "Entity name to traverse from"},
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
    {
        "name": "memory/attach_block",
        "description": "Attach a named memory block from 08 Meta/agent-context/memory-blocks/ to this session. Blocks persist for the session duration and are included in context.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "block_name": {"type": "string", "description": "Filename of block e.g. 'djinn-architecture.md'"},
                "vault_path": {"type": "string", "description": "Absolute path to vault root"},
            },
            "required": ["block_name", "vault_path"],
        },
    },
    {
        "name": "memory/list_blocks",
        "description": "List all memory blocks currently attached to this session, with character and estimated token counts.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "memory/write_working",
        "description": "Write a note to the _working/ buffer. Safe for agents — bypasses semantic write gate. Heartbeat will promote or prune.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "filename":   {"type": "string", "description": "Filename e.g. 'insight-2026-04-07.md'"},
                "content":    {"type": "string", "description": "Note content (Markdown)"},
                "vault_path": {"type": "string", "description": "Absolute path to vault root"},
                "confidence": {"type": "string", "enum": ["high", "medium", "low"], "description": "Agent confidence level", "default": "medium"},
            },
            "required": ["filename", "content", "vault_path"],
        },
    },
    {
        "name": "memory/trigger_lookup",
        "description": "Scan a message for keyword triggers and return recommended context blocks to attach from 08 Meta/agent-context/triggers.md.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "message":    {"type": "string", "description": "User message to scan for triggers"},
                "vault_path": {"type": "string", "description": "Absolute path to vault root"},
            },
            "required": ["message", "vault_path"],
        },
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
        liveness  = httpx.get(f"{daemon_url}/health", timeout=3.0).json()
        readiness = httpx.get(f"{daemon_url}/ready",  timeout=3.0).json()
        return {"liveness": liveness, "readiness": readiness}
    elif tool == "memory/attach_block":
        return _memory_attach_block(args)
    elif tool == "memory/list_blocks":
        return _memory_list_blocks()
    elif tool == "memory/write_working":
        return _memory_write_working(args)
    elif tool == "memory/trigger_lookup":
        return _memory_trigger_lookup(args)
    else:
        raise ValueError(f"Unknown tool: {tool}")


def _memory_attach_block(args: Dict) -> Dict:
    block_name = args["block_name"]
    vault_path = args["vault_path"]
    blocks_dir = Path(vault_path) / "08 Meta" / "agent-context" / "memory-blocks"
    block_file = blocks_dir / block_name
    if not block_file.exists():
        return {"error": f"Block not found: {block_file}"}
    content = block_file.read_text(encoding="utf-8")
    char_count = len(content)
    token_est  = char_count // 4
    # Avoid duplicates
    existing = [b["name"] for b in _attached_blocks]
    if block_name not in existing:
        _attached_blocks.append({
            "name":       block_name,
            "content":    content,
            "char_count": char_count,
            "token_est":  token_est,
        })
    total_tokens = sum(b["token_est"] for b in _attached_blocks)
    return {
        "attached": block_name,
        "char_count": char_count,
        "token_est": token_est,
        "session_total_tokens": total_tokens,
        "content": content,
    }


def _memory_list_blocks() -> Dict:
    total_chars  = sum(b["char_count"] for b in _attached_blocks)
    total_tokens = sum(b["token_est"]  for b in _attached_blocks)
    return {
        "attached_blocks": [
            {"name": b["name"], "char_count": b["char_count"], "token_est": b["token_est"]}
            for b in _attached_blocks
        ],
        "total_chars":  total_chars,
        "total_tokens": total_tokens,
    }


def _memory_write_working(args: Dict) -> Dict:
    filename   = args["filename"]
    content    = args["content"]
    vault_path = args["vault_path"]
    confidence = args.get("confidence", "medium")

    working_dir = Path(vault_path) / "_working"
    working_dir.mkdir(parents=True, exist_ok=True)

    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()

    frontmatter = f"""---
agent-written: true
agent-confidence: {confidence}
trust: low
importance: 0.5
decay-profile: active
date_created: {now}
status: working
---

"""
    full_content = frontmatter + content
    out_path = working_dir / filename
    out_path.write_text(full_content, encoding="utf-8")
    return {
        "written": str(out_path),
        "confidence": confidence,
        "note": "Staged in _working/. Heartbeat will promote or prune.",
    }


def _memory_trigger_lookup(args: Dict) -> Dict:
    message    = args["message"].lower()
    vault_path = args["vault_path"]
    trigger_file = (
        Path(vault_path) / "08 Meta" / "agent-context" / "triggers.md"
    )
    if not trigger_file.exists():
        return {"recommended_blocks": [], "note": "triggers.md not found"}

    triggers_raw = trigger_file.read_text(encoding="utf-8")
    recommended  = []

    # Parse markdown table rows: | pattern | block | mode |
    import re
    rows = re.findall(
        r"\|([^|]+)\|([^|]+)\|([^|]+)\|",
        triggers_raw,
    )
    for pattern_cell, block_cell, mode_cell in rows:
        pattern_cell = pattern_cell.strip()
        block_cell   = block_cell.strip()
        mode_cell    = mode_cell.strip()
        # Skip header / separator rows
        if pattern_cell.startswith("-") or pattern_cell.lower() == "keyword pattern":
            continue
        # Treat | as OR, \ as literal
        sub_patterns = [p.strip().replace("\\", "") for p in pattern_cell.split("|")]
        if any(sp and sp in message for sp in sub_patterns):
            recommended.append({
                "block": block_cell,
                "mode":  mode_cell,
                "matched_pattern": pattern_cell,
            })

    return {
        "recommended_blocks": recommended,
        "always_attach": ["identity-pvnkmnk.md"],
    }


def run_mcp_adapter(daemon_url: str):
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
            _send({"jsonrpc": "2.0", "id": msg_id, "result": {
                "protocolVersion": "2024-11-05",
                "serverInfo": {"name": "vault-memory", "version": "0.2.0"},
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
            pass

        else:
            _send({"jsonrpc": "2.0", "id": msg_id,
                   "error": {"code": -32601, "message": f"Method not found: {method}"}})
