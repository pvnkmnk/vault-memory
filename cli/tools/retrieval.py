# cli/tools/retrieval.py
"""Search-related MCP tools: search, search_siblings, graph, temporal."""

from typing import Any, Dict

from cli.mcp_client import _auth_headers

# Tool definitions for search/retrieval group
TOOLS = [
    {
        "name": "search",
        "description": "Search your Obsidian vault using semantic, keyword, graph, and temporal strategies. Returns GARS-ranked results with trust and maturity flags.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Natural language search query"},
                "project": {"type": "string", "description": "Optional: scope to a project folder"},
                "top_k": {
                    "type": "integer",
                    "description": "Number of results (default 5)",
                    "default": 5,
                },
                "include_graph": {
                    "type": "boolean",
                    "description": "Enable graph traversal strategy",
                },
                "include_temporal": {
                    "type": "boolean",
                    "description": "Enable temporal history strategy",
                },
                "apply_decay": {
                    "type": "boolean",
                    "description": "Apply temporal decay scoring (default true)",
                    "default": True,
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "search_siblings",
        "description": "Discover notes that share an Ontology topic hub with a seed note, even without direct wikilinks between them. Scored by GARS x hub_penalty.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "seed_path": {
                    "type": "string",
                    "description": "Vault-relative path of the seed note e.g. '05 Dev Projects/djinn-netrunner/djinn.md'",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max sibling results (default 10)",
                    "default": 10,
                },
                "vault_path": {"type": "string", "description": "Absolute path to vault root"},
            },
            "required": ["seed_path", "vault_path"],
        },
    },
    {
        "name": "graph",
        "description": "Traverse entity relationships in the knowledge graph.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "entity": {"type": "string", "description": "Entity name to traverse from"},
                "relationship": {
                    "type": "string",
                    "description": "Optional: filter by relationship type",
                },
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
                "start": {"type": "string", "description": "Start date YYYY-MM-DD"},
                "end": {"type": "string", "description": "End date YYYY-MM-DD"},
            },
            "required": ["entity"],
        },
    },
]


def get_tools() -> list:
    """Return the retrieval tool definitions."""
    return TOOLS
