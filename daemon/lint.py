from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Sequence
import logging
import re

logger = logging.getLogger("vault-memoryd.lint")

CONTRADICTION_REL_TYPES: Sequence[str] = (
    "status",
    "state",
    "owner",
    "maturity",
    "decision",
    "priority",
)


@dataclass
class LintReport:
    run_at: str
    stale_days: int
    orphans: List[dict]
    contradictions: List[dict]
    stale_nodes: List[dict]
    missing_pages: List[dict]
    unlinked_pages: List[dict]
    summary: Dict[str, int] = field(default_factory=dict)

    def __post_init__(self):
        self.summary = {
            "orphans": len(self.orphans),
            "contradictions": len(self.contradictions),
            "stale_nodes": len(self.stale_nodes),
            "missing_pages": len(self.missing_pages),
            "unlinked_pages": len(self.unlinked_pages),
            "total_issues": (
                len(self.orphans)
                + len(self.contradictions)
                + len(self.stale_nodes)
                + len(self.missing_pages)
                + len(self.unlinked_pages)
            ),
        }


def _query_rows(pg, sql: str, params=()) -> List[dict]:
    with pg.cursor() as cursor:
        cursor.execute(sql, params)
        return [dict(row) for row in cursor.fetchall()]


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


def _vault_page_stems(vault_root: Path) -> set[str]:
    stems = set()
    for path in vault_root.rglob("*.md"):
        if ".obsidian" in path.parts or ".trash" in path.parts:
            continue
        stems.add(_slug(path.stem))
    return stems


def _find_orphans(pg) -> List[dict]:
    sql = """
        SELECT te.entity_name, te.node_type, te.centrality
        FROM temporal_entities te
        WHERE NOT EXISTS (
            SELECT 1 FROM relationships r WHERE r.target_name = te.entity_name
        )
        ORDER BY te.entity_name
        LIMIT 200
    """
    return _query_rows(pg, sql)


def _find_contradictions(pg) -> List[dict]:
    sql = """
        SELECT
            source_name,
            relationship_type,
            array_agg(DISTINCT target_name ORDER BY target_name) AS conflicting_targets,
            COUNT(DISTINCT target_name) AS conflict_count
        FROM relationships
        WHERE relationship_type = ANY(%s)
        GROUP BY source_name, relationship_type
        HAVING COUNT(DISTINCT target_name) > 1
        ORDER BY conflict_count DESC
        LIMIT 100
    """
    return _query_rows(pg, sql, (list(CONTRADICTION_REL_TYPES),))


def _find_stale_nodes(pg, stale_days: int) -> List[dict]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=stale_days)
    sql = """
        SELECT file_path, maturity, centrality_score, last_synced_at
        FROM sync_state
        WHERE maturity != 'tree'
          AND last_synced_at < %s
          AND file_path != '__init__'
        ORDER BY last_synced_at ASC
        LIMIT 200
    """
    return _query_rows(pg, sql, (cutoff,))


def _find_missing_pages(pg, vault_root: Path) -> List[dict]:
    page_stems = _vault_page_stems(vault_root)
    rows = _query_rows(
        pg,
        """
        SELECT te.entity_name, te.node_type, te.centrality
        FROM temporal_entities te
        ORDER BY te.entity_name
        LIMIT 2000
        """,
    )
    missing = [row for row in rows if _slug(row["entity_name"]) not in page_stems]
    return missing[:200]


def _find_unlinked_pages(pg, vault_root: Path) -> List[dict]:
    inbound_rows = _query_rows(
        pg,
        """
        SELECT DISTINCT target_name
        FROM relationships
        WHERE target_name IS NOT NULL
        """,
    )
    inbound_names = {_slug(row["target_name"]) for row in inbound_rows if row.get("target_name")}

    unlinked = []
    for path in vault_root.rglob("*.md"):
        if ".obsidian" in path.parts or ".trash" in path.parts:
            continue
        stem = _slug(path.stem)
        if stem and stem not in inbound_names:
            try:
                rel = str(path.relative_to(vault_root))
            except ValueError:
                rel = str(path)
            unlinked.append({"vault_path": rel, "entity_name": path.stem})
        if len(unlinked) >= 200:
            break
    return unlinked


async def run_lint(pg, vault_root: Path, stale_days: int = 30) -> LintReport:
    orphans = _find_orphans(pg)
    contradictions = _find_contradictions(pg)
    stale_nodes = _find_stale_nodes(pg, stale_days)
    missing_pages = _find_missing_pages(pg, vault_root)
    unlinked_pages = _find_unlinked_pages(pg, vault_root)
    return LintReport(
        run_at=datetime.now(timezone.utc).isoformat(),
        stale_days=stale_days,
        orphans=orphans,
        contradictions=contradictions,
        stale_nodes=stale_nodes,
        missing_pages=missing_pages,
        unlinked_pages=unlinked_pages,
    )
