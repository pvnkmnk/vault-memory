# daemon/miner.py
"""S31 session mining — turn closed sessions into durable, project-scoped lessons.

The mining queue *is* the ``agent_sessions`` table: a session is pending when
``status = 'closed' AND mined_at IS NULL``. There is deliberately no separate
state machine to drift out of sync with the session registry.

Everything here is best-effort and offline-safe: the queue query and record
normalisation never raise, so a broken session row cannot stall the queue.
"""

import json
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger("vault-memoryd.miner")

# S31-2 buckets. Kept in sync with daemon/models/sessions.py.
RECORD_FIELDS = ("decisions", "mistakes", "discoveries", "gotchas", "workflows")

# Columns the queue needs; also the fallback order when a cursor returns tuples
# instead of dict rows.
_QUEUE_COLUMNS = ("id", "agent_name", "project", "task", "notes", "session_record", "closed_at")

MINING_QUEUE_SQL = """
    SELECT id, agent_name, project, task, notes, session_record, closed_at
    FROM agent_sessions
    WHERE status = 'closed' AND mined_at IS NULL
    ORDER BY closed_at ASC NULLS LAST
    LIMIT %s
"""


def _as_dict(row: Any, columns: tuple = _QUEUE_COLUMNS) -> Dict[str, Any]:
    """Coerce a cursor row into a dict, tolerating tuple rows."""
    if row is None:
        return {}
    if isinstance(row, dict):
        return dict(row)
    return dict(zip(columns, row))


def empty_record() -> Dict[str, List[Dict[str, Any]]]:
    """A fully-empty structured record."""
    return {name: [] for name in RECORD_FIELDS}


def normalize_record(raw: Any) -> Dict[str, List[Dict[str, Any]]]:
    """Coerce a stored ``session_record`` into ``{field: [{content, entities}]}``.

    Accepts a dict (Postgres jsonb), a JSON string (lite-mode SQLite), or
    anything malformed — an unreadable record must degrade to "no structured
    capture", never crash the miner.
    """
    if raw is None:
        return empty_record()

    if isinstance(raw, (str, bytes, bytearray)):
        try:
            raw = json.loads(raw)
        except (ValueError, TypeError):
            logger.warning("session_record is not valid JSON; treating as empty")
            return empty_record()

    if not isinstance(raw, dict):
        return empty_record()

    normalized: Dict[str, List[Dict[str, Any]]] = {}
    for name in RECORD_FIELDS:
        items = raw.get(name) or []
        if not isinstance(items, list):
            items = [items]
        bucket: List[Dict[str, Any]] = []
        for item in items:
            if isinstance(item, str):
                if item.strip():
                    bucket.append({"content": item.strip(), "entities": []})
            elif isinstance(item, dict) and item.get("content"):
                bucket.append(
                    {
                        "content": str(item["content"]).strip(),
                        "entities": [str(e) for e in (item.get("entities") or [])],
                    }
                )
        normalized[name] = bucket
    return normalized


def record_item_count(record: Dict[str, List[Dict[str, Any]]]) -> int:
    """Total captured items across all buckets."""
    return sum(len(record.get(name) or []) for name in RECORD_FIELDS)


def mining_queue(pg: Any, limit: int = 20) -> List[Dict[str, Any]]:
    """Sessions awaiting mining, oldest close first. Returns [] on any error."""
    try:
        with pg.cursor() as cursor:
            cursor.execute(MINING_QUEUE_SQL, (limit,))
            rows = cursor.fetchall()
    except Exception as e:  # noqa: BLE001 - a broken query must not kill the job
        logger.warning("mining queue query failed: %s", e)
        return []

    sessions: List[Dict[str, Any]] = []
    for row in rows or []:
        data = _as_dict(row)
        if not data:
            continue
        sessions.append(
            {
                "session_id": str(data["id"]),
                "agent_name": data.get("agent_name"),
                "project": data.get("project"),
                "task": data.get("task"),
                "notes": data.get("notes"),
                "closed_at": data.get("closed_at"),
                "record": normalize_record(data.get("session_record")),
            }
        )
    return sessions
