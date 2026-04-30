# cli/tools/knowledge.py
"""Knowledge-related MCP tools: memory/cognify, memory/promote."""

import httpx
from typing import Any, Dict

from cli.mcp_client import _auth_headers

TOOLS = [
    {
        "name": "memory/cognify",
        "description": "Run a semantic cognify pass on text. By default persists extracted triples to the graph; set persist=false for extract-only mode.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Text content to cognify"},
                "entity_types": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": 'Optional: filter by entity types e.g. ["concept", "method", "project"]',
                },
                "persist": {
                    "type": "boolean",
                    "description": "Persist extracted triples into Postgres graph (default: true)",
                    "default": True,
                },
                "daemon_url": {
                    "type": "string",
                    "description": "Daemon URL (default: http://localhost:5051)",
                    "default": "http://localhost:5051",
                },
            },
            "required": ["text"],
        },
    },
    {
        "name": "memory/promote",
        "description": "Promote wiki-quality content to a permanent Knowledge page, index it, run cognify persistence, and append a log.md audit entry.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Markdown content to promote"},
                "title": {"type": "string", "description": "Page title"},
                "page_type": {
                    "type": "string",
                    "enum": ["entity", "concept", "comparison", "analysis"],
                },
                "references": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Entity names to enforce as wikilinks in the page",
                },
                "vault_path": {"type": "string", "description": "Absolute path to vault root"},
                "daemon_url": {
                    "type": "string",
                    "description": "Daemon URL (default: http://localhost:5051)",
                    "default": "http://localhost:5051",
                },
            },
            "required": ["text", "title", "page_type", "vault_path"],
        },
    },
]


def _memory_cognify(args: Dict, daemon_url: str) -> Dict:
    daemon_url = args.get("daemon_url", daemon_url)
    text = args["text"]
    entity_types = args.get("entity_types", [])
    persist = bool(args.get("persist", True))
    payload = {"text": text, "persist": persist}
    if entity_types:
        payload["entity_types"] = entity_types
    try:
        r = httpx.post(f"{daemon_url}/cognify", json=payload, timeout=30.0, headers=_auth_headers)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        return {"error": f"cognify failed: {e}", "text_len": len(text)}


def _memory_promote(args: Dict, daemon_url: str) -> Dict:
    daemon_url = args.get("daemon_url", daemon_url)
    payload = {
        "text": args["text"],
        "title": args["title"],
        "page_type": args["page_type"],
        "references": args.get("references", []),
        "vault_path": args["vault_path"],
    }
    try:
        r = httpx.post(f"{daemon_url}/promote", json=payload, timeout=45.0, headers=_auth_headers)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        return {"error": f"promote failed: {e}", "payload_sent": payload}


def get_tools() -> list:
    """Return the knowledge tool definitions."""
    return TOOLS
