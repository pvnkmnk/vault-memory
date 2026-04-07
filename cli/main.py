# cli/main.py
"""
vault-memory: Human CLI + MCP stdio adapter.

Commands:
  vault-memory search        -- query the daemon
  vault-memory health        -- check daemon status
  vault-memory mcp           -- start MCP stdio adapter (for agents)
  vault-memory daemon start  -- start vault-memoryd
  vault-memory daemon stop   -- stop vault-memoryd
  vault-memory sync --full   -- full vault index
"""

import json
import sys
import os
import subprocess
import signal
import time
from pathlib import Path
from typing import Optional

import click
import httpx

from .sync_command import sync_command

DAEMON_URL = os.getenv("VAULT_MEMORY_URL", "http://127.0.0.1:5051")
PID_FILE   = Path.home() / ".vault-memory" / "daemon.pid"


@click.group()
def cli():
    """Vault Memory — semantic memory layer for Obsidian."""
    pass


cli.add_command(sync_command, name="sync")


# ── Daemon management ─────────────────────────────────────────────────
@cli.group()
def daemon():
    """Manage the vault-memoryd background process."""
    pass


@daemon.command("start")
@click.option("--vault", default=None, help="Path to Obsidian vault")
@click.option("--port",  default=5051,  help="Port for daemon (default: 5051)")
def daemon_start(vault, port):
    """Start vault-memoryd in the background."""
    if PID_FILE.exists():
        pid = int(PID_FILE.read_text())
        try:
            os.kill(pid, 0)
            click.echo(json.dumps({"status": "already_running", "pid": pid}))
            return
        except ProcessLookupError:
            PID_FILE.unlink()

    env = os.environ.copy()
    if vault:
        env["VAULT_PATH"] = vault
    env["VAULT_MEMORY_PORT"] = str(port)

    proc = subprocess.Popen(
        ["python", "-m", "daemon.main"],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    PID_FILE.parent.mkdir(parents=True, exist_ok=True)
    PID_FILE.write_text(str(proc.pid))
    click.echo(json.dumps({"status": "starting", "pid": proc.pid}))

    deadline = time.time() + 30
    while time.time() < deadline:
        try:
            r = httpx.get(f"http://127.0.0.1:{port}/ready", timeout=1.0)
            if r.status_code == 200:
                click.echo(json.dumps({"status": "ready", "pid": proc.pid}))
                return
        except httpx.ConnectError:
            pass
        time.sleep(0.5)

    click.echo(json.dumps({"status": "timeout", "pid": proc.pid}), err=True)
    sys.exit(1)


@daemon.command("stop")
def daemon_stop():
    """Stop vault-memoryd."""
    if not PID_FILE.exists():
        click.echo(json.dumps({"status": "not_running"}))
        return
    pid = int(PID_FILE.read_text())
    try:
        os.kill(pid, signal.SIGTERM)
        PID_FILE.unlink()
        click.echo(json.dumps({"status": "stopped", "pid": pid}))
    except ProcessLookupError:
        PID_FILE.unlink()
        click.echo(json.dumps({"status": "not_running"}))


# ── Health ────────────────────────────────────────────────────────────────────
@cli.command()
@click.option("--watch", is_flag=True, help="Poll until ready")
def health(watch):
    """Check vault-memoryd health and readiness."""
    while True:
        try:
            liveness  = httpx.get(f"{DAEMON_URL}/health", timeout=2.0).json()
            readiness = httpx.get(f"{DAEMON_URL}/ready",  timeout=2.0).json()
            click.echo(json.dumps({"liveness": liveness, "readiness": readiness}, indent=2))
            if not watch or readiness.get("status") == "ready":
                break
        except httpx.ConnectError:
            click.echo(json.dumps({"liveness": "unreachable", "readiness": "unreachable"}, indent=2))
            if not watch:
                sys.exit(1)
        time.sleep(2)


# ── Search ───────────────────────────────────────────────────────────────────
@cli.command()
@click.option("--query",   "-q", required=True)
@click.option("--project", "-p", default=None)
@click.option("--top-k",   "-k", default=5)
@click.option("--graph",         is_flag=True)
@click.option("--temporal",      is_flag=True)
@click.option("--format", "fmt", type=click.Choice(["clips", "json", "text"]), default="clips")
def search(query, project, top_k, graph, temporal, fmt):
    """Semantic search over vault."""
    try:
        r = httpx.post(
            f"{DAEMON_URL}/search",
            json={"query": query, "project": project, "top_k": top_k,
                  "include_graph": graph, "include_temporal": temporal},
            timeout=10.0,
        )
        r.raise_for_status()
        results = r.json()["results"]
        if fmt == "text":
            for res in results:
                click.echo(f"[{res['source'].upper()}] {res['path']} ({res['score']:.2f})")
                click.echo(f"  {res['snippet']}...\n")
        else:
            click.echo(json.dumps(results, indent=2))
    except httpx.ConnectError:
        click.echo(json.dumps({"error": "daemon not running — run: vault-memory daemon start"}), err=True)
        sys.exit(1)


# ── MCP stdio adapter ──────────────────────────────────────────────────────────
@cli.command()
def mcp():
    """
    Start MCP stdio adapter.
    Agents spawn this as a subprocess. Proxies JSON-RPC to the daemon.
    """
    from .mcp_adapter import MCPStdioAdapter
    import asyncio
    adapter = MCPStdioAdapter(daemon_url=DAEMON_URL)
    asyncio.run(adapter.run())


if __name__ == "__main__":
    cli()
