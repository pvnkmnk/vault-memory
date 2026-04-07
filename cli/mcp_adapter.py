# cli/mcp_adapter.py
"""
Thin MCP stdio proxy.
Reads JSON-RPC from stdin, forwards to daemon HTTP, writes response to stdout.
Startup is ~50ms because there are no model loads here.
"""

import asyncio
import json
import sys
import httpx

TOOLS = [
    {
        "name": "vault_search",
        "description": "Semantic search over Obsidian vault (dense + sparse + rerank)",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query":            {"type": "string"},
                "project":          {"type": "string"},
                "top_k":            {"type": "integer", "default": 5},
                "include_graph":    {"type": "boolean"},
                "include_temporal": {"type": "boolean"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "vault_graph_query",
        "description": "Query entity relationships in vault knowledge graph",
        "inputSchema": {
            "type": "object",
            "properties": {
                "entity":       {"type": "string"},
                "relationship": {"type": "string"},
            },
            "required": ["entity"],
        },
    },
    {
        "name": "vault_temporal_query",
        "description": "Query temporal evolution of entity over time",
        "inputSchema": {
            "type": "object",
            "properties": {
                "entity": {"type": "string"},
                "start":  {"type": "string"},
                "end":    {"type": "string"},
            },
            "required": ["entity"],
        },
    },
    {
        "name": "vault_health",
        "description": "Check vault memory system health and readiness",
        "inputSchema": {"type": "object", "properties": {}},
    },
]


class MCPStdioAdapter:
    def __init__(self, daemon_url: str):
        self.daemon_url = daemon_url

    async def run(self):
        reader   = asyncio.StreamReader()
        protocol = asyncio.StreamReaderProtocol(reader)
        loop     = asyncio.get_event_loop()
        await loop.connect_read_pipe(lambda: protocol, sys.stdin.buffer)

        async with httpx.AsyncClient(timeout=30.0) as client:
            self.client = client
            while True:
                try:
                    line = await reader.readline()
                    if not line:
                        break
                    request  = json.loads(line.decode())
                    response = await self._handle(request)
                    sys.stdout.write(json.dumps(response) + "\n")
                    sys.stdout.flush()
                except Exception as e:
                    self._send_error(None, -32603, str(e))

    async def _handle(self, req: dict) -> dict:
        method = req.get("method")
        params = req.get("params", {})
        rid    = req.get("id")

        if method == "tools/list":
            return {"jsonrpc": "2.0", "result": {"tools": TOOLS}, "id": rid}

        if method == "tools/call":
            name = params.get("name")
            args = params.get("arguments", {})

            if name == "vault_search":
                r       = await self.client.post(f"{self.daemon_url}/search", json=args)
                results = r.json()["results"]
                text    = "\n---\n".join(
                    f"[{res['source'].upper()}] {res['path']} ({res['score']:.2f})\n{res['snippet']}..."
                    for res in results
                )
                return {"jsonrpc": "2.0", "result": {"content": [{"type": "text", "text": text}]}, "id": rid}

            elif name == "vault_graph_query":
                r = await self.client.get(f"{self.daemon_url}/graph",
                                          params={"entity": args["entity"],
                                                  "relationship": args.get("relationship")})
                return {"jsonrpc": "2.0", "result": {"content": [{"type": "text", "text": json.dumps(r.json())}]}, "id": rid}

            elif name == "vault_temporal_query":
                r = await self.client.get(f"{self.daemon_url}/temporal",
                                          params={"entity": args["entity"],
                                                  "start": args.get("start", "2025-01-01"),
                                                  "end":   args.get("end",   "2026-12-31")})
                return {"jsonrpc": "2.0", "result": {"content": [{"type": "text", "text": json.dumps(r.json())}]}, "id": rid}

            elif name == "vault_health":
                r = await self.client.get(f"{self.daemon_url}/ready")
                return {"jsonrpc": "2.0", "result": {"content": [{"type": "text", "text": json.dumps(r.json())}]}, "id": rid}

        return {"jsonrpc": "2.0", "error": {"code": -32601, "message": f"Unknown method: {method}"}, "id": rid}

    def _send_error(self, rid, code, message):
        sys.stdout.write(json.dumps({"jsonrpc": "2.0", "error": {"code": code, "message": message}, "id": rid}) + "\n")
        sys.stdout.flush()
