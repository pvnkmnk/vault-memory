# cli/main.py
"""
vault-memory: Human CLI + MCP stdio adapter.

Commands:
  vault-memory search        -- query the daemon
  vault-memory health        -- check daemon status
  vault-memory mcp           -- start MCP stdio adapter
  vault-memory sync          -- full vault sync
  vault-memory ingest        -- ingest a doc/URL/text into the vault
  vault-memory lessons       -- review mined lesson drafts
  vault-memory digest        -- daily/weekly/monthly digests
  vault-memory skills        -- export/list agent skill bundles
  vault-memory prune         -- soft-prune stale notes
  vault-memory heartbeat     -- run heartbeat manually
  vault-memory daemon start  -- start vault-memoryd
  vault-memory daemon stop   -- stop vault-memoryd
"""

import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import click
import httpx

from .sync_command import sync_command

DAEMON_URL = os.getenv("VAULT_MEMORY_URL", "http://127.0.0.1:5051")
PID_FILE   = Path.home() / ".vault-memory" / "daemon.pid"


def looks_like_url(value: str) -> bool:
    """Local check so the CLI never has to import the daemon package."""
    return bool(re.match(r"^https?://", (value or "").strip(), re.IGNORECASE))


def _daemon_headers() -> dict:
    """Auth header for daemon calls made outside the MCP adapter."""
    key = os.getenv("VAULT_MEMORY_API_KEY", "")
    return {"x-api-key": key} if key else {}


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


# ── sessions ─────────────────────────────────────────────────────────────────

@cli.group("sessions")
def sessions_group():
    """Agent session registry — mining and inspection."""


@sessions_group.command("mine")
@click.option("--limit", default=5, help="Max sessions to mine in one run")
def sessions_mine(limit):
    """Distil closed sessions into lesson drafts (S31-3).

    Lesson drafts land in _working/sessions/ with review: pending. Nothing is
    written to the wiki until a human promotes it.
    """
    try:
        r = httpx.post(
            f"{DAEMON_URL}/sessions/mine",
            params={"limit": limit},
            timeout=900.0,
            headers=_daemon_headers(),
        )
        r.raise_for_status()
        data = r.json()
    except httpx.ConnectError:
        click.echo("Error: vault-memoryd is not running. Run: vault-memory daemon start", err=True)
        sys.exit(1)
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)

    click.echo(
        f"Queued: {data.get('queued', 0)}  mined: {data.get('mined', 0)}  "
        f"failed: {data.get('failed', 0)}  drafts: {data.get('drafts', 0)}"
    )
    for result in data.get("results", []):
        if result.get("status") == "failed":
            click.echo(f"  ! {result.get('session_id')}: {result.get('error')}", err=True)
        for draft in result.get("drafts", []):
            click.echo(f"  + {draft}")
        for slug in result.get("corroborated", []):
            click.echo(f"  ~ corroborates {slug}")


# ── lessons ──────────────────────────────────────────────────────────────────

@cli.group("lessons")
def lessons_group():
    """Lesson review gate — inspect, promote, and reject mined drafts."""


def _lessons_request(method, path, **kwargs):
    """Daemon call shared by the lesson commands; exits on failure."""
    try:
        r = getattr(httpx, method)(
            f"{DAEMON_URL}{path}", timeout=30.0, headers=_daemon_headers(), **kwargs
        )
        r.raise_for_status()
        return r.json()
    except httpx.ConnectError:
        click.echo("Error: vault-memoryd is not running. Run: vault-memory daemon start", err=True)
        sys.exit(1)
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)


@lessons_group.command("list")
@click.option("--project", default=None, help="Filter to one project slug")
@click.option("--top-k", default=5, help="Max lessons to show")
def lessons_list(project, top_k):
    """Ranked promoted lessons (recency x corroboration x trust)."""
    params = {"top_k": top_k}
    if project:
        params["project"] = project
    data = _lessons_request("get", "/lessons", params=params)

    if not data.get("lessons"):
        click.echo("No promoted lessons yet.")
        return
    for item in data["lessons"]:
        click.echo(
            f"{item['score']:.4f}  {item['slug']}  "
            f"(corroboration={item['corroboration']}, trust={item['trust']})"
        )
    click.echo(f"{data['tokens_used']}/{data['token_budget']} tokens")


@lessons_group.command("review")
@click.option("--project", default=None, help="Filter to one project slug")
def lessons_review(project):
    """Mined drafts awaiting a decision."""
    params = {"review": "pending"}
    if project:
        params["project"] = project
    data = _lessons_request("get", "/lessons/review", params=params)

    if not data.get("drafts"):
        click.echo("Nothing pending review.")
        return
    for draft in data["drafts"]:
        click.echo(f"{draft['name']}  [{draft['kind']}]  corroboration={draft['corroboration']}")
        click.echo(f"    {draft['title']}")
    click.echo(
        f"\n{data['count']} pending. Auto-promote policy: {data['auto_promote_policy']}"
    )


@lessons_group.command("promote")
@click.argument("name")
@click.option("--reviewer", default=None, help="Reviewer name for the audit trail")
def lessons_promote(name, reviewer):
    """Accept a draft into lessons/."""
    payload = {"name": name}
    if reviewer:
        payload["reviewer"] = reviewer
    result = _lessons_request("post", "/lessons/promote", json=payload)
    click.echo(f"Promoted: {result['path']}")


@lessons_group.command("reject")
@click.argument("name")
@click.option("--reason", required=True, help="Why (fed into the next mining prompt)")
@click.option("--reviewer", default=None, help="Reviewer name for the audit trail")
def lessons_reject(name, reason, reviewer):
    """Reject a draft and record the reason."""
    payload = {"name": name, "reason": reason}
    if reviewer:
        payload["reviewer"] = reviewer
    result = _lessons_request("post", "/lessons/reject", json=payload)
    click.echo(f"Rejected: {result['path']}")


# ── ingest ───────────────────────────────────────────────────────────────────

@cli.command("ingest")
@click.argument("source", required=False)
@click.option("--text", default=None, help="Ingest pasted text instead of a path/URL")
@click.option("--vault", default=None, help="Vault root (default: $VAULT_MEMORY_VAULT_PATH)")
@click.option("--inbox", "drain_inbox", is_flag=True, help="Process everything in inbox/")
@click.option("--status", "show_status", is_flag=True, help="Show the ingest manifest")
@click.option("--limit", default=10, help="Max inbox files to process")
@click.option("--remove", is_flag=True, help="Delete inbox files after they compile")
@click.option("--force", is_flag=True, help="Recompile even when the content is unchanged")
def ingest(source, text, vault, drain_inbox, show_status, limit, remove, force):
    """Ingest a document, URL, or pasted text into the knowledge base (S32-1).

    Files outside the vault are copied into inbox/ first, so the daemon only
    ever reads paths inside the vault.
    """
    vault_root = vault or os.getenv("VAULT_MEMORY_VAULT_PATH")
    if vault_root is None:
        vault_root = str(Path.home() / "vault")
    vault_root = Path(vault_root).expanduser().resolve()

    if show_status:
        data = _lessons_request("get", "/ingest/manifest")
        if not data.get("sources"):
            click.echo("Nothing ingested yet.")
        for ref, entry in (data.get("sources") or {}).items():
            state = "compiled" if entry.get("compiled_at") else "archived (compile failed)"
            click.echo(f"{state:28} {ref}")
        for item in data.get("inbox_pending") or []:
            click.echo(f"{'inbox':28} {item['file']}")
        return

    if drain_inbox:
        data = _lessons_request(
            "post",
            "/ingest/inbox",
            json={"limit": limit, "force": force, "remove": remove},
        )
        click.echo(
            f"Queued: {data['queued']}  compiled: {data['compiled']}  "
            f"skipped: {data['skipped']}  failed: {data['failed']}"
        )
        for result in data["results"]:
            _report_ingest(result)
        return

    if not source and text is None:
        click.echo("Error: give a path, a URL, --text, --inbox, or --status", err=True)
        sys.exit(1)

    payload = {"force": force}
    if text is not None:
        payload["text"] = text
    elif looks_like_url(source):
        payload["url"] = source
    else:
        incoming = Path(source).expanduser().resolve()
        if not incoming.is_file():
            click.echo(f"Error: not a file: {source}", err=True)
            sys.exit(1)
        try:
            incoming.relative_to(vault_root)
        except ValueError:
            # Outside the vault: stage it in inbox/ so the daemon stays confined.
            inbox = vault_root / "inbox"
            inbox.mkdir(parents=True, exist_ok=True)
            target = inbox / incoming.name
            if target.exists() and target.read_bytes() != incoming.read_bytes():
                stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
                target = inbox / f"{incoming.stem}-{stamp}{incoming.suffix}"
            shutil.copy2(incoming, target)
            click.echo(f"Staged in inbox/: {target.relative_to(vault_root)}")
            incoming = target
        payload["path"] = str(incoming.relative_to(vault_root))

    _report_ingest(_lessons_request("post", "/ingest", json=payload))


def _report_ingest(result):
    status = result.get("status", "?")
    click.echo(f"{status}: {result.get('raw_path') or result.get('source') or ''}")
    if result.get("error"):
        click.echo(f"  ! {result['error']}", err=True)
    if result.get("reason"):
        click.echo(f"  ({result['reason']})")
    for path in result.get("pages_created", []):
        click.echo(f"  + {path}")
    for path in result.get("pages_updated", []):
        click.echo(f"  ~ {path}")
    for conflict in result.get("conflicts", []):
        click.echo(f"  ! conflict, not overwritten: {conflict.get('path')} ({conflict.get('reason')})", err=True)
    if result.get("triples"):
        click.echo(f"  triples: {result['triples']} ({result.get('relationships_written', 0)} new edges)")
    tags = result.get("claim_tags") or {}
    if any(tags.values()):
        click.echo(
            f"  claims: extracted={tags.get('extracted', 0)} "
            f"inferred={tags.get('inferred', 0)} ambiguous={tags.get('ambiguous', 0)}"
        )


# ── digest ───────────────────────────────────────────────────────────────────

@cli.group("digest")
def digest_group():
    """Digests — daily overview, weekly deep dive, monthly consolidation."""


def _run_digest(kind, summarise):
    data = _lessons_request(
        "post", f"/digest/{kind}", json={"summarise": summarise}
    )
    click.echo(f"{data['path']}  ({data['pages_changed']} pages, {data['sessions']} sessions)")
    if data.get("pending_drafts"):
        click.echo(f"  {data['pending_drafts']} lesson draft(s) awaiting review")
    if data.get("ingested_sources"):
        click.echo(f"  {data['ingested_sources']} ingested source(s) in window")
    for theme in data.get("themes", []):
        click.echo(f"  theme: {theme}")
    for proposal in data.get("proposals", []):
        click.echo(f"  + {proposal['path']}")
    if not data.get("summarised"):
        click.echo("  (no LLM summary)")


@digest_group.command("daily")
@click.option("--no-summary", is_flag=True, help="Skip the LLM summary")
def digest_daily(no_summary):
    """Nightly overview: what changed, what was learned, what needs attention."""
    _run_digest("daily", not no_summary)


@digest_group.command("weekly")
@click.option("--no-summary", is_flag=True, help="Skip the LLM summary")
def digest_weekly(no_summary):
    """In-depth week: velocity, corroboration, emerging entities."""
    _run_digest("weekly", not no_summary)


@digest_group.command("monthly")
@click.option("--no-summary", is_flag=True, help="Skip the LLM summary")
def digest_monthly(no_summary):
    """Consolidate the month's corroborated lessons into skill proposals."""
    _run_digest("monthly", not no_summary)


# ── skills ───────────────────────────────────────────────────────────────────

@cli.group("skills")
def skills_group():
    """Agent skills bundles exported from the vault's lesson corpus."""


@skills_group.command("export")
@click.option("--project", default=None, help="Export only one project's lessons")
@click.option(
    "--min-corroboration",
    default=1,
    help="Only export lessons corroborated at least this many times",
)
def skills_export(project, min_corroboration):
    """Write skills/<theme>/SKILL.md bundles (never overwrites hand-written ones)."""
    payload = {"min_corroboration": min_corroboration}
    if project:
        payload["project"] = project
    data = _lessons_request("post", "/skills/export", json=payload)

    click.echo(f"Themes: {data['themes']}  written: {len(data['written'])}  staged: {len(data['staged'])}")
    for item in data["written"]:
        click.echo(f"  + {item['path']}  ({len(item['lessons'])} lessons)")
    for item in data["staged"]:
        click.echo(f"  ~ {item['path']}  ({item['reason']})", err=True)
    for path in data.get("missing_pages") or []:
        click.echo(f"  ! referenced page missing: {path}", err=True)


@skills_group.command("list")
def skills_list():
    """List exported skill bundles."""
    data = _lessons_request("get", "/skills")
    if not data.get("skills"):
        click.echo("No skills exported yet. Run: vault-memory skills export")
        return
    for item in data["skills"]:
        marker = "" if item.get("generated") else "  (hand-written, not regenerated)"
        click.echo(f"{item['name']}  {item['path']}{marker}")


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
                            new_fm += "\nstatus: stale"
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
    result = subprocess.run(["bash", "--", str(script), f"--mode={mode}"], cwd=vault)
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
@click.option("--daemon-url", default=None, help="Vault-memory daemon URL (default: $VAULT_MEMORY_URL or http://127.0.0.1:5051)")
@click.option("--api-key", default=None, help="API key for daemon authentication")
def mcp(daemon_url, api_key):
    """Start the MCP stdio adapter (for AI agents)."""
    from .mcp_adapter import run_mcp_adapter
    effective_url = daemon_url or DAEMON_URL
    run_mcp_adapter(daemon_url=effective_url, api_key=api_key)


# ── tui ───────────────────────────────────────────────────────────────────────

@cli.command("tui")
@click.option("--daemon-url", default=None, help="Vault-memory daemon URL (default: $VAULT_MEMORY_URL or http://127.0.0.1:5051)")
@click.option("--api-key", default=None, help="API key for daemon authentication")
def tui(daemon_url, api_key):
    """Launch the Terminal User Interface (TUI) for vault-memory."""
    if daemon_url:
        os.environ["VAULT_MEMORY_URL"] = daemon_url
    if api_key:
        os.environ["VAULT_MEMORY_API_KEY"] = api_key
    from .tui.app import VaultMemoryTUI
    app = VaultMemoryTUI()
    app.run()


# ── sync ──────────────────────────────────────────────────────────────────────
cli.add_command(sync_command)


if __name__ == "__main__":
    cli()
