# cli/proxy.py
"""
HTTP proxy helpers for CLI → daemon communication.
"""
import httpx

DAEMON_URL_DEFAULT = "http://127.0.0.1:5051"


def get_daemon_url(port: int = 5051) -> str:
    import os
    return os.getenv("VAULT_MEMORY_URL", f"http://127.0.0.1:{port}")


def daemon_get(path: str, **params) -> dict:
    url = get_daemon_url()
    r = httpx.get(f"{url}{path}", params=params, timeout=10.0)
    r.raise_for_status()
    return r.json()


def daemon_post(path: str, payload: dict) -> dict:
    url = get_daemon_url()
    r = httpx.post(f"{url}{path}", json=payload, timeout=30.0)
    r.raise_for_status()
    return r.json()
