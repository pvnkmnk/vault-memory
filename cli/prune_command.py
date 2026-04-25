"""
vault-memory prune  — P4 Sprint

Soft-flags stale notes by setting `status: stale` in frontmatter.
Does NOT delete files.

Usage:
  vault-memory prune --vault ~/vault --max-age 90 --dry-run   # preview
  vault-memory prune --vault ~/vault --max-age 90             # execute

A note is considered stale when:
  - Its frontmatter `date_modified` (or file mtime as fallback) is older
    than --max-age days, AND
  - Its frontmatter `status` is NOT already one of: stale, archived, reference
    (identity / reference notes are exempt)
  - Its frontmatter `decay-profile` is NOT `identity` or `reference`
"""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Tuple

import click
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

console = Console(stderr=True)

# Statuses that are exempt from pruning
EXEMPT_STATUSES = {"stale", "archived", "reference", "identity"}
# Decay profiles exempt from pruning
EXEMPT_PROFILES = {"identity", "reference"}


# ---------------------------------------------------------------------------
# Frontmatter helpers
# ---------------------------------------------------------------------------

def _parse_frontmatter(text: str) -> Tuple[Dict, str]:
    """
    Parse YAML frontmatter block.  Returns (meta_dict, body_text).
    Very lightweight — not a full YAML parser, sufficient for our keys.
    """
    meta: Dict = {}
    body = text
    if not text.startswith("---"):
        return meta, body
    end = text.find("\n---", 3)
    if end == -1:
        return meta, body
    fm_block = text[3:end].strip()
    body     = text[end + 4:]
    for line in fm_block.splitlines():
        m = re.match(r"^([\w_\-]+)\s*:\s*(.*)", line)
        if m:
            meta[m.group(1)] = m.group(2).strip().strip('"').strip("'")
    return meta, body


def _set_frontmatter_key(text: str, key: str, value: str) -> str:
    """
    Set a single key in the frontmatter block.  Adds the key if absent.
    """
    if not text.startswith("---"):
        return f"---\n{key}: {value}\n---\n\n{text}"
    end = text.find("\n---", 3)
    if end == -1:
        return text
    fm_block = text[3:end]
    body     = text[end + 4:]
    # Replace existing key
    pattern = re.compile(r"^(" + re.escape(key) + r"\s*:.*)$", re.MULTILINE)
    if pattern.search(fm_block):
        fm_block = pattern.sub(f"{key}: {value}", fm_block)
    else:
        fm_block = fm_block.rstrip() + f"\n{key}: {value}"
    return "---" + fm_block + "\n---" + body


# ---------------------------------------------------------------------------
# Core prune logic
# ---------------------------------------------------------------------------

@click.command("prune")
@click.option("--vault",     required=True, help="Absolute path to vault root")
@click.option("--max-age",   default=90, show_default=True, help="Age threshold in days")
@click.option("--dry-run",   is_flag=True, help="Preview only — do not modify files")
@click.option("--output",    default="stdout", type=click.Choice(["stdout", "file"]))
def prune_command(vault: str, max_age: int, dry_run: bool, output: str):
    """
    Soft-flag stale notes (set status: stale in frontmatter).
    Does NOT delete files.
    """
    vault_path = Path(vault).expanduser().resolve()
    if not vault_path.exists():
        console.print(f"[red]✗[/] Vault not found: {vault_path}")
        sys.exit(1)

    now = datetime.now(timezone.utc)

    mode_label = "[yellow]DRY RUN[/]" if dry_run else "[red]LIVE[/]"
    console.print(Panel.fit(
        f"[bold cyan]vault-memory prune[/] {mode_label}\n"
        f"Vault:    [white]{vault_path}[/]\n"
        f"Max age:  [white]{max_age} days[/]\n"
        f"Mode:     {mode_label}",
        border_style="cyan",
    ))

    md_files = [
        p for p in vault_path.rglob("*.md")
        if not p.name.startswith(".")
        and ".obsidian" not in p.parts
        and ".trash"    not in p.parts
        and "_working"  not in p.parts
    ]

    flagged:  List[Dict] = []
    skipped:  List[Dict] = []
    errors:   List[Dict] = []

    for path in md_files:
        rel = str(path.relative_to(vault_path))
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
            meta, _ = _parse_frontmatter(text)

            # Exempt checks
            status  = meta.get("status", "active").lower()
            profile = meta.get("decay-profile", meta.get("decay_profile", "active")).lower()
            if status in EXEMPT_STATUSES or profile in EXEMPT_PROFILES:
                skipped.append({"path": rel, "reason": f"exempt status/profile: {status}/{profile}"})
                continue

            # Determine note age
            date_mod_str = meta.get("date_modified") or meta.get("modified") or meta.get("updated")
            if date_mod_str:
                try:
                    dt = datetime.fromisoformat(date_mod_str.replace("Z", "+00:00"))
                    note_ts = dt.timestamp()
                except ValueError:
                    note_ts = path.stat().st_mtime
            else:
                note_ts = path.stat().st_mtime

            age_days = (now.timestamp() - note_ts) / 86400
            if age_days <= max_age:
                continue  # fresh — skip

            flagged.append({"path": rel, "age_days": round(age_days, 1)})

            if not dry_run:
                updated = _set_frontmatter_key(text, "status", "stale")
                path.write_text(updated, encoding="utf-8")

        except Exception as e:
            errors.append({"path": rel, "error": str(e)})

    # ── Summary ────────────────────────────────────────────────────────────
    action = "would flag" if dry_run else "flagged"
    console.print()
    if dry_run:
        console.print("[yellow]DRY RUN — no files modified[/]")
    console.print(f"[bold]{action.capitalize()}:[/] {len(flagged)} notes as stale  "
                  f"| Skipped: {len(skipped)} (exempt) | Errors: {len(errors)}")

    if flagged:
        table = Table(title=f"Notes {action} as stale", border_style="yellow", show_header=True)
        table.add_column("Path", style="dim")
        table.add_column("Age (days)", justify="right")
        for item in flagged[:50]:
            table.add_row(item["path"], str(item["age_days"]))
        if len(flagged) > 50:
            table.add_row(f"... and {len(flagged) - 50} more", "")
        console.print(table)

    if errors:
        console.print("\n[red]Errors:[/]")
        for e in errors[:10]:
            console.print(f"  [dim]{e['path']}[/]: {e['error']}")

    summary = {
        "status":       "dry_run" if dry_run else "complete",
        "vault_path":   str(vault_path),
        "max_age_days": max_age,
        "dry_run":      dry_run,
        "flagged":      flagged,
        "skipped_count": len(skipped),
        "error_count":  len(errors),
        "errors":       errors,
    }

    json_out = json.dumps(summary, indent=2)
    if output == "file":
        from datetime import datetime as _dt
        ts = _dt.now().strftime("%Y%m%d-%H%M%S")
        out_path = Path(f"prune-report-{ts}.json")
        out_path.write_text(json_out)
        console.print(f"\n[dim]Report: {out_path}[/]")
    else:
        print(json_out)

    sys.exit(1 if errors else 0)
