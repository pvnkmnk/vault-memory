# daemon/helpers/attribution.py
"""Session attribution — resolve ``X-Session-Id`` and record file touches.

S31-1: ``sync_log`` is the evidence table the session miner reads to answer
"what did this session actually change?". Two rules govern this module:

1. **Attribution is best-effort.** A failure to record a touch must never fail
   the write it is describing, so every DB call here swallows and logs.
2. **Only known sessions are recorded.** An ``X-Session-Id`` that does not
   resolve against ``agent_sessions`` is ignored (with a warning) rather than
   inserting a dangling row — the FK would reject it anyway.
"""

import logging
from typing import Any, Dict, Optional

logger = logging.getLogger("vault-memoryd.attribution")

SESSION_ID_HEADER = "x-session-id"

ACTIONS = frozenset({"created", "modified", "deleted", "promoted"})


def resolve_session(deps: Any, session_id: Optional[str]) -> Optional[Dict[str, Any]]:
    """Validate ``session_id`` against ``agent_sessions``.

    Returns ``{"id", "agent_name", "project"}`` for a known session, else None.
    Never raises.
    """
    if not session_id:
        return None
    try:
        with deps.postgres.cursor() as cursor:
            cursor.execute(
                "SELECT id, agent_name, project FROM agent_sessions WHERE id = %s",
                (str(session_id),),
            )
            row = cursor.fetchone()
    except Exception as e:  # noqa: BLE001 - attribution must not break the caller
        logger.warning("session lookup failed for %s: %s", session_id, e)
        return None

    if not row:
        logger.warning("X-Session-Id %s does not match a known session; ignoring", session_id)
        return None

    # The daemon uses RealDictCursor, but stay tolerant of tuple rows so this
    # helper cannot break attribution if the cursor factory ever changes.
    if isinstance(row, dict):
        session = {
            "id": row["id"],
            "agent_name": row.get("agent_name"),
            "project": row.get("project"),
        }
    else:  # (id, agent_name, project)
        session = {"id": row[0], "agent_name": row[1], "project": row[2]}

    session["id"] = str(session["id"])
    return session


def session_from_request(request: Any, deps: Any) -> Optional[Dict[str, Any]]:
    """Read ``X-Session-Id`` off a Starlette request and resolve it."""
    if request is None:
        return None
    return resolve_session(deps, request.headers.get(SESSION_ID_HEADER))


def log_file_action(
    deps: Any,
    session: Optional[Dict[str, Any]],
    file_path: str,
    action: str,
) -> bool:
    """Append one ``sync_log`` row for a session-tagged write.

    Returns True when a row was written. Never raises.
    """
    if action not in ACTIONS:
        logger.warning("ignoring unknown sync_log action %r", action)
        return False
    if not session or not file_path:
        return False

    try:
        with deps.postgres.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO sync_log (session_id, file_path, action, agent_name)
                VALUES (%s, %s, %s, %s)
                """,
                (session["id"], str(file_path), action, session.get("agent_name")),
            )
        return True
    except Exception as e:  # noqa: BLE001 - best-effort by design
        logger.warning("sync_log insert failed (%s %s): %s", action, file_path, e)
        return False


def action_for_write(existed_before: bool) -> str:
    """Map "did this path already exist?" onto a sync_log action."""
    return "modified" if existed_before else "created"
