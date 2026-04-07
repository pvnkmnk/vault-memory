# cli/main.py
"""
vault-memory: Human CLI + MCP stdio adapter.

Commands:
  vault-memory search        -- query the daemon
  vault-memory health        -- check daemon status
  vault-memory mcp           -- start MCP stdio adapter
  vault-memory sync          -- full vault sync
  vault-memory prune         -- soft-prune stale notes (v0.2.0)
  vault-memory heartbeat     -- run heartbeat manually (v0.2.0)
  vault-memory daemon start  -- start vault-memoryd
  vault-memory daemon stop   -- stop vault-memoryd
"""

import json
import os
import signal
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
@click.option("--no-decay",    is_flag=True,    help="Disable temporal decay scoring")
@click.option("--tag",         multiple=True,   help="Filter by tag (repeatable)")
@click.option("--format",      default="text",  type=click.Choice(["text", "json", "clips"]), help="Output format")
def search(query, project, top_k, graph, temporal, no_decay, tag, format):
    """Search your vault using the 4-strategy pipeline."""
    payload = {
        "query":            query,
        "project":          project,
        "top_k":            top_k,
        "include_graph":    graph,
        "include_temporal": temporal,
        "apply_decay":      not no_decay,
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

    click.echo(f"\nQuery: {query!r}  intent={intent}\n")
    for i, r in enumerate(results, 1):
        strategies   = ", ".join(r.get("sources", [r.get("source", "?")]))
        trust_badge  = f"[trust:{r.get('trust','?')}]"
        agent_badge  = " [agent]" if r.get("agent_written") else ""
        click.echo(f"{i}. {r['path']}  [score: {r['score']:.3f}] {trust_badge}{agent_badge}")
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
            liveness  = httpx.get(f"{DAEMON_URL}/health", timeout=3.0).json()
            readiness = httpx.get(f"{DAEMON_URL}/ready",  timeout=3.0).json()
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


# ── prune ─────────────────────────────────────────────────────────────────────

@cli.command("prune")
@click.option("--vault",          required=True, help="Path to vault root")
@click.option("--max-age",        default=90,    help="Max age in days before flagging as stale")
@click.option("--min-importance", default=0.3,   type=float, help="Minimum importance score to retain")
@click.option("--dry-run",        is_flag=True,  help="Show what would be flagged without writing")
def prune(vault, max_age, min_importance, dry_run):
    """Soft-prune stale notes by flagging with status: stale."""
    from datetime import datetime, timedelta
    import re

    vault_path = Path(vault)
    cutoff     = datetime.now() - timedelta(days=max_age)
    flagged    = 0
    FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)

    for md_file in vault_path.rglob("*.md"):
        rel = md_file.relative_to(vault_path)
        # Skip _working/, 08 Meta/heartbeat/, templates/
        parts = rel.parts
        if parts[0].startswith("_") or "heartbeat" in str(rel) or parts[0] == "templates":
            continue
        try:
            raw = md_file.read_text(encoding="utf-8", errors="replace")
            # Parse importance from frontmatter
            importance = 1.0
            fm_match = FRONTMATTER_RE.match(raw)
            if fm_match:
                for line in fm_match.group(1).splitlines():
                    if line.strip().startswith("importance:"):
                        try:
                            importance = float(line.split(":", 1)[1].strip())
                        except ValueError:
                            pass

            modified = datetime.fromtimestamp(md_file.stat().st_mtime)
            if modified < cutoff and importance < min_importance:
                if dry_run:
                    click.echo(f"[dry-run] would flag: {rel}  (age={( datetime.now()-modified).days}d, importance={importance})")
                else:
                    # Inject/update status: stale in frontmatter
                    now_iso = datetime.now().isoformat()
                    if fm_match:
                        new_fm = fm_match.group(1)
                        if "status:" in new_fm:
                            new_fm = re.sub(r"status:\s*\S+", "status: stale", new_fm)
                        else:
                            new_fm += f"\nstatus: stale"
                        new_fm += f"\npruned-at: {now_iso}"
                        new_raw = f"---\n{new_fm}\n---\n" + raw[fm_match.end():]
                    else:
                        new_raw = f"---\nstatus: stale\npruned-at: {now_iso}\n---\n\n" + raw
                    md_file.write_text(new_raw, encoding="utf-8")
                    click.echo(f"Flagged stale: {rel}")
                flagged += 1
        except Exception as e:
            logger_msg = f"Skipped {rel}: {e}"
            click.echo(logger_msg, err=True)
            continue

    action = "would flag" if dry_run else "flagged"
    click.echo(f"\nPrune complete: {action} {flagged} notes older than {max_age}d with importance < {min_importance}")


# ── heartbeat ─────────────────────────────────────────────────────────────────

@cli.command("heartbeat")
@click.option("--mode",  default="daily",
              type=click.Choice(["daily", "weekly", "autonomous"]),
              help="Heartbeat mode (default: daily)")
@click.option("--vault", required=True, help="Path to vault root")
def heartbeat(mode, vault):
    """Run the heartbeat scheduler manually."""
    script = Path(vault) / "homelab-bridge" / "heartbeat.sh"
    if not script.exists():
        click.echo(
            f"heartbeat.sh not found at {script}.\n"
            "Copy it from the creativebrain-obsidian-vault-template repo: homelab-bridge/heartbeat.sh",
            err=True,
        )
        sys.exit(1)
    result = subprocess.run(["bash", str(script), f"--mode={mode}"], cwd=vault)
    sys.exit(result.returncode)


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


# ── sync ──────────────────────────────────────────────────────────────────────
cli.add_command(sync_command)


if __name__ == "__main__":
    cli()
