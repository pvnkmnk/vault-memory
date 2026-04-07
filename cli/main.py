# cli/main.py
"""
vault-memory: Human CLI + MCP stdio adapter.

Commands:
  vault-memory search        -- query the daemon
  vault-memory health        -- check daemon status
  vault-memory mcp           -- start MCP stdio adapter
  vault-memory sync          -- full vault sync
  vault-memory daemon start  -- start vault-memoryd
  vault-memory daemon stop   -- stop vault-memoryd
"""

import json
import os
import subprocess
import sys
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
    """Vault Memory — always-on local memory layer for Obsidian."""


# ── search ────────────────────────────────────────────────────────────────────

@cli.command("search")
@click.option("-q", "--query",  required=True, help="Search query")
@click.option("-p", "--project",               help="Scope to project")
@click.option("--top-k",       default=5,       help="Number of results")
@click.option("--graph",       is_flag=True,    help="Enable graph strategy")
@click.option("--temporal",    is_flag=True,    help="Enable temporal strategy")
@click.option("--tag",         multiple=True,   help="Filter by tag (repeatable)")
@click.option("--format",      default="text",  type=click.Choice(["text", "json", "clips"]), help="Output format")
def search(query, project, top_k, graph, temporal, tag, format):
    """Search your vault using the 4-strategy pipeline."""
    payload = {
        "query":            query,
        "project":          project,
        "top_k":            top_k,
        "include_graph":    graph,
        "include_temporal": temporal,
        "tags":             list(tag) if tag else None,
    }
    try:
        r = httpx.post(f"{DAEMON_URL}/search", json=payload, timeout=30.0)
        r.raise_for_status()
        data = r.json()
    except httpx.ConnectError:
        click.echo("Error: vault-memoryd is not running. Run: vault-memory daemon start", err=True)
        sys.exit(1)
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)

    results = data.get("results", [])
    intent  = data.get("intent", "unknown")

    if format == "json":
        click.echo(json.dumps(data, indent=2))
        return

    if not results:
        click.echo(f"No results for: {query!r}  (intent: {intent})")
        return

    if format == "clips":
        for r in results:
            click.echo(json.dumps(r))
        return

    # text format
    click.echo(f"\nQuery: {query!r}  intent={intent}\n")
    for i, r in enumerate(results, 1):
        strategies = ", ".join(r.get("sources", [r.get("source", "?")])) 
        click.echo(f"{i}. {r['path']}  [score: {r['score']:.3f}]")
        click.echo(f"   Strategies: {strategies}")
        if r.get("tags"):
            click.echo(f"   Tags: {' '.join('#' + t for t in r['tags'])}")
        if r.get("modified"):
            click.echo(f"   Modified: {r['modified']}")
        click.echo(f"   {r.get('snippet', '')}")
        click.echo()


# ── health ────────────────────────────────────────────────────────────────────

@cli.command("health")
@click.option("--watch",  is_flag=True, help="Poll until ready")
@click.option("--format", default="text", type=click.Choice(["text", "json"]))
def health(watch, format):
    """Check vault-memoryd liveness and readiness."""
    def _check():
        try:
            liveness  = httpx.get(f"{DAEMON_URL}/health",  timeout=3.0).json()
            readiness = httpx.get(f"{DAEMON_URL}/ready",   timeout=3.0).json()
            return {"liveness": liveness, "readiness": readiness}
        except Exception as e:
            return {"error": str(e)}

    if watch:
        click.echo("Watching daemon health (Ctrl+C to stop)...")
        while True:
            result = _check()
            status = result.get("readiness", {}).get("status", "unknown")
            click.echo(f"  {status}")
            if status == "ready":
                break
            time.sleep(2.0)

    result = _check()
    if format == "json":
        click.echo(json.dumps(result, indent=2))
    else:
        if "error" in result:
            click.echo(f"Daemon unreachable: {result['error']}")
        else:
            r = result.get("readiness", {})
            click.echo(f"Status:   {r.get('status', '?')}")
            click.echo(f"Uptime:   {r.get('uptime_seconds', 0):.1f}s")
            click.echo(f"Last idx: {r.get('last_index', 'never')}")


# ── graph ─────────────────────────────────────────────────────────────────────

@cli.command("graph")
@click.option("--entity", required=True, help="Entity name to traverse from")
@click.option("--rel",                   help="Filter by relationship type")
def graph(entity, rel):
    """Graph traversal from a named entity."""
    params = {"entity": entity}
    if rel:
        params["relationship"] = rel
    try:
        r = httpx.get(f"{DAEMON_URL}/graph", params=params, timeout=10.0)
        click.echo(json.dumps(r.json(), indent=2))
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)


# ── temporal ──────────────────────────────────────────────────────────────────

@cli.command("temporal")
@click.option("--entity", required=True)
@click.option("--start",  default="2025-01-01")
@click.option("--end",    default="2026-12-31")
def temporal(entity, start, end):
    """Time-range query for entity history."""
    try:
        r = httpx.get(f"{DAEMON_URL}/temporal",
                      params={"entity": entity, "start": start, "end": end},
                      timeout=10.0)
        click.echo(json.dumps(r.json(), indent=2))
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)


# ── daemon ────────────────────────────────────────────────────────────────────

@cli.group("daemon")
def daemon_group():
    """Manage vault-memoryd lifecycle."""


@daemon_group.command("start")
def daemon_start():
    """Start vault-memoryd in the background."""
    PID_FILE.parent.mkdir(parents=True, exist_ok=True)
    if PID_FILE.exists():
        pid = int(PID_FILE.read_text().strip())
        try:
            os.kill(pid, 0)
            click.echo(f"vault-memoryd already running (PID {pid})")
            return
        except ProcessLookupError:
            PID_FILE.unlink()
    proc = subprocess.Popen(
        ["vault-memoryd"],
        stdout=open(Path.home() / ".vault-memory" / "daemon.log", "a"),
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    PID_FILE.write_text(str(proc.pid))
    click.echo(f"vault-memoryd started (PID {proc.pid})")
    click.echo("Run: vault-memory health --watch")


@daemon_group.command("stop")
def daemon_stop():
    """Stop vault-memoryd."""
    if not PID_FILE.exists():
        click.echo("vault-memoryd is not running")
        return
    pid = int(PID_FILE.read_text().strip())
    import signal
    try:
        os.kill(pid, signal.SIGTERM)
        PID_FILE.unlink()
        click.echo(f"vault-memoryd stopped (PID {pid})")
    except ProcessLookupError:
        PID_FILE.unlink()
        click.echo("Process not found — cleaned up PID file")


@daemon_group.command("status")
def daemon_status():
    """Show daemon PID and uptime."""
    if not PID_FILE.exists():
        click.echo("vault-memoryd: not running")
        return
    pid = int(PID_FILE.read_text().strip())
    try:
        os.kill(pid, 0)
        click.echo(f"vault-memoryd: running (PID {pid})")
    except ProcessLookupError:
        click.echo("vault-memoryd: PID file exists but process not found")


@daemon_group.command("logs")
@click.option("-n", "--lines", default=50, help="Number of tail lines")
def daemon_logs(lines):
    """Tail daemon logs."""
    log_file = Path.home() / ".vault-memory" / "daemon.log"
    if not log_file.exists():
        click.echo("No log file found")
        return
    all_lines = log_file.read_text().splitlines()
    for line in all_lines[-lines:]:
        click.echo(line)


# ── mcp ───────────────────────────────────────────────────────────────────────

@cli.command("mcp")
def mcp():
    """Start the MCP stdio adapter (for AI agents)."""
    from .mcp_adapter import run_mcp_adapter
    run_mcp_adapter(daemon_url=DAEMON_URL)


# ── add sync command ──────────────────────────────────────────────────────────
cli.add_command(sync_command)


if __name__ == "__main__":
    cli()
