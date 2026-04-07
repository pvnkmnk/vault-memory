# cli/proxy.py
"""Thin HTTP proxy helpers for CLI commands."""

import httpx
from typing import Any, Dict, Optional


class DaemonProxy:
    def __init__(self, base_url: str = "http://127.0.0.1:5051"):
        self.base_url = base_url
        self.client   = httpx.Client(timeout=30.0)

    def search(self, query: str, **kwargs) -> Dict[str, Any]:
        return self.client.post(f"{self.base_url}/search",
                                json={"query": query, **kwargs}).json()

    def health(self) -> Dict[str, Any]:
        liveness  = self.client.get(f"{self.base_url}/health").json()
        readiness = self.client.get(f"{self.base_url}/ready").json()
        return {"liveness": liveness, "readiness": readiness}

    def graph(self, entity: str, relationship: Optional[str] = None) -> Dict:
        params = {"entity": entity}
        if relationship:
            params["relationship"] = relationship
        return self.client.get(f"{self.base_url}/graph", params=params).json()

    def temporal(self, entity: str, start: str, end: str) -> Dict:
        return self.client.get(f"{self.base_url}/temporal",
                               params={"entity": entity, "start": start, "end": end}).json()

    def close(self):
        self.client.close()
