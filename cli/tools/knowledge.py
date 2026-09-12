# cli/tools/knowledge.py
"""Knowledge-related MCP tools: memory/cognify, memory/promote."""

import httpx
from typing import Any, Dict

from cli import mcp_client

TOOLS = [
    {
        "name": "memory/cognify",
        "description": "Run a semantic cognify pass on text using the daemon's configured LLM provider (Ollama or llama.cpp/OpenAI-compatible). By default persists extracted triples to the graph; set persist=false for extract-only mode.",
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
    {
        "name": "memory/ingest",
        "description": "Ingest a document, URL, or pasted text into the knowledge base (S32-1). Archives it immutably under raw/ then compiles it into Knowledge/ pages with claim provenance. Local paths must be inside the vault.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Vault-relative path to a .md/.txt/.pdf file (mutually exclusive with url/text)"},
                "url": {"type": "string", "description": "http(s) URL to fetch and readability-extract (mutually exclusive with path/text)"},
                "text": {"type": "string", "description": "Pasted markdown/text to ingest (mutually exclusive with path/url)"},
                "force": {"type": "boolean", "description": "Recompile even when the content hash is unchanged", "default": False},
                "daemon_url": {
                    "type": "string",
                    "description": "Daemon URL (default: http://localhost:5051)",
                    "default": "http://localhost:5051",
                },
            },
        },
    },
    {
        "name": "memory/lesson_review",
        "description": "List mined lesson drafts awaiting review (_working/sessions), with their corroboration counts. Use before promoting or rejecting so the decision is informed.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "review": {
                    "type": "string",
                    "enum": ["pending", "approved", "rejected", "all"],
                    "description": "Which drafts to list (default: pending)",
                    "default": "pending",
                },
                "project": {"type": "string", "description": "Optional project slug filter"},
                "daemon_url": {
                    "type": "string",
                    "description": "Daemon URL (default: http://localhost:5051)",
                    "default": "http://localhost:5051",
                },
            },
        },
    },
    {
        "name": "memory/lesson_promote",
        "description": "Accept a mined lesson draft into lessons/ (review: approved, maturity seed -> sapling). Pass the draft name from memory/lesson_review.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Draft name/relative path from memory/lesson_review"},
                "reviewer": {"type": "string", "description": "Optional reviewer identifier for the audit trail"},
                "daemon_url": {
                    "type": "string",
                    "description": "Daemon URL (default: http://localhost:5051)",
                    "default": "http://localhost:5051",
                },
            },
            "required": ["name"],
        },
    },
    {
        "name": "memory/lesson_reject",
        "description": "Reject a mined lesson draft and store the reason. The reason is injected into the next mining prompt for that project, so a specific reason makes future drafts better.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Draft name/relative path from memory/lesson_review"},
                "reason": {
                    "type": "string",
                    "description": "Why it was rejected (required, max 500 chars). Be specific: this feeds future mining.",
                },
                "reviewer": {"type": "string", "description": "Optional reviewer identifier for the audit trail"},
                "daemon_url": {
                    "type": "string",
                    "description": "Daemon URL (default: http://localhost:5051)",
                    "default": "http://localhost:5051",
                },
            },
            "required": ["name", "reason"],
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
        r = httpx.post(
            f"{daemon_url}/cognify",
            json=payload,
            timeout=30.0,
            headers=mcp_client._auth_headers,
        )
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
        r = httpx.post(
            f"{daemon_url}/promote",
            json=payload,
            timeout=45.0,
            headers=mcp_client._auth_headers,
        )
        r.raise_for_status()
        return r.json()
    except Exception as e:
        return {"error": f"promote failed: {e}", "payload_sent": payload}


def _memory_ingest(args: Dict, daemon_url: str) -> Dict:
    daemon_url = args.get("daemon_url", daemon_url)
    payload = {key: args[key] for key in ("path", "url", "text") if args.get(key)}
    if not payload:
        return {"error": "provide exactly one of: path, url, text"}
    payload["force"] = bool(args.get("force", False))
    try:
        r = httpx.post(
            f"{daemon_url}/ingest",
            json=payload,
            timeout=300.0,
            headers=mcp_client._auth_headers,
        )
        r.raise_for_status()
        return r.json()
    except Exception as e:
        return {"error": f"ingest failed: {e}", "payload_sent": payload}


def _lesson_review(args: Dict, daemon_url: str) -> Dict:
    daemon_url = args.get("daemon_url", daemon_url)
    params = {"review": args.get("review", "pending")}
    if args.get("project"):
        params["project"] = args["project"]
    try:
        r = httpx.get(
            f"{daemon_url}/lessons/review",
            params=params,
            timeout=30.0,
            headers=mcp_client._auth_headers,
        )
        r.raise_for_status()
        return r.json()
    except Exception as e:
        return {"error": f"lesson review failed: {e}", "params": params}


def _lesson_promote(args: Dict, daemon_url: str) -> Dict:
    daemon_url = args.get("daemon_url", daemon_url)
    payload = {"name": args["name"]}
    if args.get("reviewer"):
        payload["reviewer"] = args["reviewer"]
    try:
        r = httpx.post(
            f"{daemon_url}/lessons/promote",
            json=payload,
            timeout=30.0,
            headers=mcp_client._auth_headers,
        )
        r.raise_for_status()
        return r.json()
    except Exception as e:
        return {"error": f"lesson promote failed: {e}", "payload_sent": payload}


def _lesson_reject(args: Dict, daemon_url: str) -> Dict:
    daemon_url = args.get("daemon_url", daemon_url)
    payload = {"name": args["name"], "reason": args["reason"]}
    if args.get("reviewer"):
        payload["reviewer"] = args["reviewer"]
    try:
        r = httpx.post(
            f"{daemon_url}/lessons/reject",
            json=payload,
            timeout=30.0,
            headers=mcp_client._auth_headers,
        )
        r.raise_for_status()
        return r.json()
    except Exception as e:
        return {"error": f"lesson reject failed: {e}", "payload_sent": payload}


def get_tools() -> list:
    """Return the knowledge tool definitions."""
    return TOOLS
