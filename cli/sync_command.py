# cli/sync_command.py
"""
vault-memory sync --full

Standalone full-vault indexing command.
Runs independently of the daemon — useful for first-time setup,
forced re-index, CI/CD pipelines, and crash recovery.
"""

import asyncio
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import click
import httpx
from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TaskProgressColumn,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
)
from rich.table import Table
from rich.text import Text

console = Console(stderr=True)

DAEMON_URL_DEFAULT = "http://127.0.0.1:5051"
WEAVIATE_URL_DEFAULT = "http://127.0.0.1:8080"
PG_CONN_DEFAULT = "dbname=vault_memory user=vault password=vault_local host=localhost"


def _wait_for_service(url: str, name: str, timeout: int = 30) -> bool:
    deadline = time.time() + timeout
    with console.status(f"[bold yellow]Waiting for {name}...[/]"):
        while time.time() < deadline:
            try:
                r = httpx.get(url, timeout=2.0)
                if r.status_code == 200:
                    console.print(f"  [green]✓[/] {name} ready")
                    return True
            except Exception:
                pass
            time.sleep(1.0)
    console.print(f"  [red]✗[/] {name} did not become ready within {timeout}s")
    return False


def _check_postgres() -> bool:
    try:
        import psycopg2

        conn = psycopg2.connect(PG_CONN_DEFAULT, connect_timeout=5)
        conn.close()
        console.print("  [green]✓[/] PostgreSQL ready")
        return True
    except Exception as e:
        console.print(f"  [red]✗[/] PostgreSQL unavailable: {e}")
        return False


def check_services(weaviate_url: str) -> bool:
    console.print("\n[bold]Checking services...[/]")
    weaviate_ok = _wait_for_service(f"{weaviate_url}/v1/.well-known/ready", "Weaviate", timeout=30)
    pg_ok = _check_postgres()
    return weaviate_ok and pg_ok


def make_progress() -> Progress:
    return Progress(
        SpinnerColumn(),
        TextColumn("[bold blue]{task.description}"),
        BarColumn(bar_width=40),
        MofNCompleteColumn(),
        TaskProgressColumn(),
        TimeElapsedColumn(),
        TimeRemainingColumn(),
        console=console,
        transient=False,
    )


def make_stats_table(stats: dict) -> Table:
    table = Table.grid(padding=(0, 2))
    table.add_column(style="bold cyan", justify="right")
    table.add_column()
    table.add_row("Files processed:", str(stats.get("files_done", 0)))
    table.add_row("Files skipped:", str(stats.get("files_skipped", 0)))
    table.add_row("Files errored:", str(stats.get("files_errored", 0)))
    table.add_row("Chunks created:", str(stats.get("chunks_total", 0)))
    table.add_row("Elapsed:", f"{stats.get('elapsed', 0.0):.1f}s")
    table.add_row("Rate:", f"{stats.get('rate', 0.0):.1f} files/s")
    if stats.get("current_file"):
        table.add_row("Current:", Text(Path(stats["current_file"]).name, style="italic dim"))
    return table


async def run_full_sync(
    vault_path,
    weaviate_url,
    pg_conn_str,
    embedding_model,
    reranker_model,
    force,
    batch_size,
) -> dict:
    from daemon.weaviate_client import WeaviateClient
    from daemon.pg_client import PostgresClient
    from daemon.embedder import EmbedderService
    from daemon.sync_watcher import SyncEngine

    console.print("\n[bold]Loading models...[/] (10–20s on first run)")
    with console.status("[yellow]Initialising...[/]"):
        weaviate_client = WeaviateClient(weaviate_url)
        pg_client = PostgresClient(pg_conn_str)
        embedder = EmbedderService(embedding_model=embedding_model, reranker_model=reranker_model)
        engine = SyncEngine(vault_path, weaviate_client, pg_client, embedder)
    console.print("  [green]✓[/] Models loaded\n")

    if force:
        console.print("[yellow]--force: clearing existing sync state[/]")
        engine.state.file_hashes.clear()
        engine.state.last_full_sync = None
        engine._save_state()

    md_files = [
        p
        for p in vault_path.rglob("*.md")
        if not p.name.startswith(".") and ".obsidian" not in p.parts and ".trash" not in p.parts
    ]
    total_files = len(md_files)
    console.print(f"[bold]Found {total_files} Markdown files[/] in [cyan]{vault_path}[/]\n")

    stats = {
        "files_done": 0,
        "files_skipped": 0,
        "files_errored": 0,
        "chunks_total": 0,
        "elapsed": 0.0,
        "rate": 0.0,
        "current_file": "",
        "errors": [],
    }
    start_time = time.monotonic()
    progress = make_progress()
    task_id = progress.add_task("Indexing vault", total=total_files)

    with Live(console=console, refresh_per_second=10) as live:

        def _refresh():
            stats["elapsed"] = time.monotonic() - start_time
            done = stats["files_done"] + stats["files_skipped"]
            stats["rate"] = done / stats["elapsed"] if stats["elapsed"] > 0 else 0.0
            layout = Table.grid(expand=True)
            layout.add_column()
            layout.add_row(progress)
            layout.add_row(make_stats_table(stats))
            live.update(
                Panel(layout, title="[bold cyan]vault-memory sync --full[/]", border_style="cyan")
            )

        for batch_start in range(0, total_files, batch_size):
            batch = md_files[batch_start : batch_start + batch_size]
            for path in batch:
                rel = str(path.relative_to(vault_path))
                stats["current_file"] = rel
                try:
                    chunks = await engine.sync_file(path)
                    if chunks == 0 and engine.state.file_hashes.get(rel):
                        stats["files_skipped"] += 1
                    else:
                        stats["files_done"] += 1
                        stats["chunks_total"] += chunks
                except Exception as e:
                    stats["files_errored"] += 1
                    stats["errors"].append({"file": rel, "error": str(e)})
                progress.advance(task_id)
                _refresh()
            engine._save_state()

    engine.state.last_full_sync = datetime.now(timezone.utc).isoformat()
    engine._save_state()
    total_elapsed = time.monotonic() - start_time
    summary = {
        "status": "complete",
        "vault_path": str(vault_path),
        "total_files": total_files,
        "files_indexed": stats["files_done"],
        "files_skipped": stats["files_skipped"],
        "files_errored": stats["files_errored"],
        "chunks_created": stats["chunks_total"],
        "elapsed_seconds": round(total_elapsed, 2),
        "rate_files_per_s": round(stats["rate"], 2),
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "errors": stats["errors"],
    }
    weaviate_client.close()
    pg_client.close()
    return summary


def _print_summary(summary: dict):
    console.print()
    if summary["files_errored"] == 0:
        console.print("[bold green]✓ Sync complete[/]")
    else:
        console.print(f"[bold yellow]⚠ Sync complete with {summary['files_errored']} error(s)[/]")
    table = Table(title="Sync Summary", border_style="cyan", show_header=False)
    table.add_column(style="bold cyan", justify="right")
    table.add_column()
    table.add_row("Total files:", str(summary["total_files"]))
    table.add_row("Indexed:", f"[green]{summary['files_indexed']}[/]")
    table.add_row("Skipped:", f"[dim]{summary['files_skipped']} (unchanged)[/]")
    table.add_row(
        "Errored:",
        f"[red]{summary['files_errored']}[/]" if summary["files_errored"] else "[dim]0[/]",
    )
    table.add_row("Chunks:", str(summary["chunks_created"]))
    table.add_row("Time:", f"{summary['elapsed_seconds']}s")
    table.add_row("Rate:", f"{summary['rate_files_per_s']} files/s")
    table.add_row("Completed:", summary["completed_at"])
    console.print(table)
    if summary["errors"]:
        console.print("\n[red]Errors:[/]")
        for err in summary["errors"][:10]:
            console.print(f"  [dim]{err['file']}[/]: {err['error']}")


@click.command("sync")
@click.option("--full", is_flag=True, required=False)
@click.option("--check-drift", is_flag=True, help="Check for hot/cold drift without re-indexing")
@click.option("--drift-only", is_flag=True, help="Re-index only drifted files (fast reconcile)")
@click.option("--vault", default=None)
@click.option("--force", is_flag=True)
@click.option("--weaviate-url", default=WEAVIATE_URL_DEFAULT)
@click.option("--pg-conn", default=PG_CONN_DEFAULT)
@click.option("--embedding-model", default="sentence-transformers/e5-large")
@click.option("--reranker-model", default="mixedbread-ai/mxbai-rerank-large-v1")
@click.option("--batch-size", default=20)
@click.option("--output", default="stdout", type=click.Choice(["stdout", "file"]))
@click.option("--no-check", is_flag=True)
def sync_command(
    full,
    check_drift,
    drift_only,
    vault,
    force,
    weaviate_url,
    pg_conn,
    embedding_model,
    reranker_model,
    batch_size,
    output,
    no_check,
):
    """Full vault sync: chunk, embed, and index all notes.

    Drift detection:
      --check-drift   Show files with hot/cold drift
      --drift-only    Re-index only drifted files (fast reconcile)
    """
    """Full vault sync: chunk, embed, and index all notes."""
    import os as _os

    if vault:
        vault_path = Path(vault).expanduser().resolve()
    else:
        env_vault = _os.getenv("VAULT_PATH")
        vault_path = Path(env_vault).expanduser().resolve() if env_vault else None
        if not vault_path:
            for candidate in [
                Path.cwd() / ".vault-memory.json",
                Path.home() / ".vault-memory.json",
            ]:
                if candidate.exists():
                    cfg = json.loads(candidate.read_text())
                    if cfg.get("vault_path"):
                        vault_path = Path(cfg["vault_path"]).expanduser().resolve()
                        break
    if not vault_path or not vault_path.exists():
        console.print(
            "[red]✗ Vault path not found.[/] Set with --vault, $VAULT_PATH, or .vault-memory.json"
        )
        sys.exit(1)

    # P4 Sprint: Drift detection handlers
    if check_drift:
        console.print(
            Panel.fit(
                "[bold yellow]vault-memory sync --check-drift[/]",
                border_style="yellow",
            )
        )
        drift_files = _detect_drift(pg_conn)
        if not drift_files:
            console.print("[green]✓ No drift detected — vault is in sync.[/]")
        else:
            table = Table(title=f"Drift Detection: {len(drift_files)} files", border_style="yellow")
            table.add_column("File", style="cyan")
            table.add_column("Status", style="yellow")
            table.add_column("Modified", style="dim")
            for f in drift_files[:50]:
                table.add_row(f["name"] or f["file_path"], f["status"], f["date_modified"] or "N/A")
            console.print(table)
            if len(drift_files) > 50:
                console.print(f"[dim]... and {len(drift_files) - 50} more[/]")
        print(json.dumps({"drift_count": len(drift_files), "files": drift_files}))
        sys.exit(0)

    if drift_only:
        console.print(
            Panel.fit(
                "[bold yellow]vault-memory sync --drift-only[/]",
                border_style="yellow",
            )
        )
        if not no_check:
            if not check_services(weaviate_url):
                console.print("\n[red]Services unavailable.[/] Run: [bold]docker compose up -d[/]")
                sys.exit(1)
        try:
            result = _reindex_drifted(
                vault_path=vault_path,
                pg_conn_str=pg_conn,
                weaviate_url=weaviate_url,
                embedding_model=embedding_model,
                reranker_model=reranker_model,
                batch_size=batch_size,
                force=force,
            )
        except KeyboardInterrupt:
            console.print("\n[yellow]Drift reconcile interrupted.[/]")
            sys.exit(130)
        print(json.dumps(result))
        sys.exit(1 if result["files_errored"] > 0 else 0)

    # Require --full for regular sync
    if not full:
        console.print("[red]✗ Missing required flag. Use --full, --check-drift, or --drift-only[/]")
        console.print("  --full        Full vault sync")
        console.print("  --check-drift  Check for drift")
        console.print("  --drift-only  Re-index drifted files only")
        sys.exit(1)

    console.print(
        Panel.fit(
            f"[bold cyan]vault-memory sync --full[/]\nVault: [white]{vault_path}[/]\nForce: [white]{force}[/]\nBatch: [white]{batch_size} files[/]",
            border_style="cyan",
        )
    )

    if not no_check:
        if not check_services(weaviate_url):
            console.print("\n[red]Services unavailable.[/] Run: [bold]docker compose up -d[/]")
            sys.exit(1)

    try:
        summary = asyncio.run(
            run_full_sync(
                vault_path=vault_path,
                weaviate_url=weaviate_url,
                pg_conn_str=pg_conn,
                embedding_model=embedding_model,
                reranker_model=reranker_model,
                force=force,
                batch_size=batch_size,
            )
        )
    except KeyboardInterrupt:
        console.print("\n[yellow]Sync interrupted. Progress saved — re-run to continue.[/]")
        sys.exit(130)

    _print_summary(summary)
    json_out = json.dumps(summary, indent=2)
    if output == "file":
        from datetime import datetime as _dt

        ts = _dt.now().strftime("%Y%m%d-%H%M%S")
        out_path = Path(f"sync-report-{ts}.json")
        out_path.write_text(json_out)
        console.print(f"\n[dim]Report written to {out_path}[/]")
    else:
        print(json_out)

    sys.exit(1 if summary["files_errored"] > 0 else 0)


# -----------------------------------------------------------------------------
# P4 Sprint: Drift Detection CLI
# -----------------------------------------------------------------------------


def _detect_drift(pg_conn_str: str) -> list[dict]:
    """
    Query sync_state for files with hot/cold drift.
    Drift = content_hash != cold_store_hash (file modified after last index)
    """
    import psycopg2

    conn = psycopg2.connect(pg_conn_str)
    cursor = conn.cursor()

    # Query: files where hashes don't match OR never indexed
    sql = """
        SELECT file_path, name, content_hash, cold_store_hash, date_modified
        FROM sync_state
        WHERE cold_store_hash IS NULL
           OR cold_store_hash != content_hash
        ORDER BY date_modified DESC
    """
    cursor.execute(sql)
    rows = cursor.fetchall()
    cursor.close()
    conn.close()

    drift_files = []
    for row in rows:
        file_path, name, content_hash, cold_store_hash, date_modified = row
        status = "never_indexed" if cold_store_hash is None else "modified"
        drift_files.append(
            {
                "file_path": file_path,
                "name": name,
                "content_hash": content_hash,
                "cold_store_hash": cold_store_hash,
                "status": status,
                "date_modified": date_modified.isoformat() if date_modified else None,
            }
        )
    return drift_files


def _reindex_drifted(
    vault_path: Path,
    pg_conn_str: str,
    weaviate_url: str,
    embedding_model: str,
    reranker_model: str,
    batch_size: int,
    force: bool,
) -> dict:
    """
    Re-index only files with drift (fast reconcile).
    """
    import psycopg2
    from daemon.sync_watcher import SyncEngine
    from daemon.weaviate_client import WeaviateClient
    from daemon.pg_client import PostgresClient
    from daemon.embedder import EmbedderService
    from rich.table import Table

    # Get drifted files
    drift_files = _detect_drift(pg_conn_str)
    if not drift_files:
        console.print("[green]No drift detected — vault is in sync.[/]")
        return {"files_reindexed": 0, "files_errored": 0}

    console.print(f"[yellow]Found {len(drift_files)} drifted files[/]\n")

    # Show drift table
    table = Table(title="Drift Detection Results", border_style="yellow")
    table.add_column("File", style="cyan")
    table.add_column("Status", style="yellow")
    table.add_column("Modified", style="dim")
    for f in drift_files[:20]:  # Show first 20
        table.add_row(f["name"] or f["file_path"], f["status"], f["date_modified"] or "N/A")
    console.print(table)
    if len(drift_files) > 20:
        console.print(f"[dim]... and {len(drift_files) - 20} more[/]\n")

    # Re-index each drifted file
    console.print("[bold]Re-indexing drifted files...[/]\n")
    weaviate_client = WeaviateClient(weaviate_url)
    pg_client = PostgresClient(pg_conn_str)
    embedder = EmbedderService(embedding_model=embedding_model, reranker_model=reranker_model)
    engine = SyncEngine(vault_path, weaviate_client, pg_client, embedder)

    reindexed = 0
    errored = 0
    for drift_file in drift_files:
        file_path = drift_file["file_path"]
        try:
            full_path = vault_path / file_path
            if full_path.exists():
                chunks = asyncio.run(engine.sync_file(full_path))
                reindexed += 1
                console.print(f"  [green]✓[/] {file_path}")
            else:
                # File deleted from disk - mark for removal
                console.print(f"  [yellow]→[/] {file_path} (deleted on disk)")
        except Exception as e:
            errored += 1
            console.print(f"  [red]✗[/] {file_path}: {e}")

    console.print(f"\n[bold]Drift reconcile complete:[/] {reindexed} re-indexed, {errored} errors")
    return {"files_reindexed": reindexed, "files_errored": errored}
