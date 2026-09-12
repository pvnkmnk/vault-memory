# tests/test_s31_attribution.py
"""S31-1 (issue #75): session attribution through the ``sync_log`` table.

Locks down the four things that were broken or missing:

- ``sync_log`` exists in the schema at all (it was queried but never created).
- ``GET /sessions/{id}/attribution`` degrades sanely: 404 for unknown sessions,
  ``source=sync_log`` + per-action counts for real ones.
- ``POST /sessions/{id}/log`` validates the session and records the touch.
- The MCP adapter's headers reach ``cli/tools/*`` (they did not: the header
  dict was rebound, so importers kept the original empty dict).
"""

import asyncio
import re
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from daemon.helpers import attribution
from daemon.models.sessions import SessionLogRequest


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _deps(fetchone=None, fetchall=None):
    """Dependencies stub whose postgres cursor is a context manager."""
    cursor = MagicMock()
    cursor.fetchone.return_value = fetchone
    cursor.fetchall.return_value = fetchall if fetchall is not None else []
    postgres = MagicMock()
    postgres.cursor.return_value.__enter__.return_value = cursor
    deps = SimpleNamespace(
        postgres=postgres,
        settings=SimpleNamespace(lite_mode=False),
    )
    return deps, cursor


SESSION_ROW = {"id": "11111111-1111-1111-1111-111111111111", "agent_name": "claude-code", "project": "vault-memory"}


# ---------------------------------------------------------------------------
# schema
# ---------------------------------------------------------------------------

def test_sync_log_table_exists_with_required_columns():
    """The bug: sync_log was queried by the attribution endpoint but absent."""
    sql = Path("init_db.sql").read_text(encoding="utf-8")
    assert "CREATE TABLE IF NOT EXISTS sync_log" in sql

    body = sql.split("CREATE TABLE IF NOT EXISTS sync_log", 1)[1].split(");", 1)[0]
    for column in ("session_id", "file_path", "action", "agent_name", "created_at"):
        assert re.search(rf"\b{column}\b", body), f"sync_log missing column {column}"

    # The action vocabulary the write paths rely on.
    for action in ("created", "modified", "deleted", "promoted"):
        assert action in body

    assert "REFERENCES agent_sessions(id)" in body
    assert "idx_sync_log_session" in sql


# ---------------------------------------------------------------------------
# resolve_session
# ---------------------------------------------------------------------------

def test_resolve_session_returns_none_without_a_header():
    deps, _ = _deps()
    assert attribution.resolve_session(deps, None) is None
    assert attribution.resolve_session(deps, "") is None
    # No DB round-trip for an absent header.
    deps.postgres.cursor.assert_not_called()


def test_resolve_session_ignores_unknown_ids():
    deps, _ = _deps(fetchone=None)
    assert attribution.resolve_session(deps, "no-such-session") is None


def test_resolve_session_normalizes_known_ids():
    deps, _ = _deps(fetchone=dict(SESSION_ROW))
    session = attribution.resolve_session(deps, SESSION_ROW["id"])
    assert session["id"] == SESSION_ROW["id"]
    assert isinstance(session["id"], str)
    assert session["agent_name"] == "claude-code"


def test_resolve_session_swallows_db_errors():
    deps = SimpleNamespace(postgres=MagicMock())
    deps.postgres.cursor.side_effect = RuntimeError("db down")
    assert attribution.resolve_session(deps, SESSION_ROW["id"]) is None


# ---------------------------------------------------------------------------
# log_file_action
# ---------------------------------------------------------------------------

def test_log_file_action_inserts_expected_row():
    deps, cursor = _deps()
    assert attribution.log_file_action(
        deps, {"id": SESSION_ROW["id"], "agent_name": "claude-code"},
        "_working/insight.md", "created",
    ) is True

    sql, params = cursor.execute.call_args[0]
    assert "INSERT INTO sync_log" in sql
    assert params == (SESSION_ROW["id"], "_working/insight.md", "created", "claude-code")


def test_log_file_action_needs_a_session_and_a_path():
    deps, cursor = _deps()
    assert attribution.log_file_action(deps, None, "a.md", "created") is False
    assert attribution.log_file_action(deps, SESSION_ROW, "", "created") is False
    cursor.execute.assert_not_called()


def test_log_file_action_rejects_unknown_actions():
    deps, cursor = _deps()
    assert attribution.log_file_action(deps, SESSION_ROW, "a.md", "shrugged") is False
    cursor.execute.assert_not_called()


def test_log_file_action_never_raises():
    """Attribution is best-effort: a broken DB must not fail the write."""
    deps = SimpleNamespace(postgres=MagicMock())
    deps.postgres.cursor.side_effect = RuntimeError("db down")
    assert attribution.log_file_action(deps, SESSION_ROW, "a.md", "created") is False


def test_action_for_write():
    assert attribution.action_for_write(False) == "created"
    assert attribution.action_for_write(True) == "modified"


# ---------------------------------------------------------------------------
# routes
# ---------------------------------------------------------------------------

def test_attribution_404s_for_unknown_session():
    from daemon.routes.sessions import session_attribution

    deps, _ = _deps(fetchone=None)
    res = asyncio.run(session_attribution("ghost", deps=deps, _auth="ok"))
    assert res.status_code == 404


def test_attribution_reports_source_and_counts():
    from daemon.routes.sessions import session_attribution

    rows = [
        {"file_path": "_working/a.md", "action": "created", "agent_name": "claude-code", "created_at": None},
        {"file_path": "_working/b.md", "action": "modified", "agent_name": "claude-code", "created_at": None},
        {"file_path": "concept-x.md", "action": "promoted", "agent_name": "claude-code", "created_at": None},
    ]
    deps, _ = _deps(fetchone=dict(SESSION_ROW), fetchall=rows)
    res = asyncio.run(
        session_attribution(SESSION_ROW["id"], deps=deps, _auth="ok")
    )
    assert res["source"] == "sync_log"
    assert res["count"] == 3
    assert res["by_action"] == {"created": 1, "modified": 1, "promoted": 1}


def test_session_log_endpoint_records_a_touch():
    from daemon.routes.sessions import session_log

    deps, cursor = _deps(fetchone=dict(SESSION_ROW))
    res = asyncio.run(
        session_log(
            SESSION_ROW["id"],
            SessionLogRequest(file_path="_working/a.md", action="created"),
            deps=deps,
            _auth="ok",
        )
    )
    assert res["logged"] is True
    assert res["action"] == "created"
    assert "INSERT INTO sync_log" in cursor.execute.call_args[0][0]


def test_session_log_endpoint_404s_for_unknown_session():
    from daemon.routes.sessions import session_log

    deps, cursor = _deps(fetchone=None)
    res = asyncio.run(
        session_log(
            "ghost",
            SessionLogRequest(file_path="_working/a.md"),
            deps=deps,
            _auth="ok",
        )
    )
    assert res.status_code == 404
    # The lookup ran, but nothing was written for an unknown session.
    executed = [call.args[0] for call in cursor.execute.call_args_list]
    assert executed == [
        "SELECT id, agent_name, project FROM agent_sessions WHERE id = %s"
    ]


# ---------------------------------------------------------------------------
# MCP adapter plumbing
# ---------------------------------------------------------------------------

def test_tool_modules_never_bind_the_header_dict_by_value():
    """Guard against the regression class, not just the one instance.

    ``cli/tools/*`` used to do ``from cli.mcp_client import _auth_headers``,
    which froze the empty dict at import time. Anything that rebound the global
    (production code did, and tests/test_mcp_auth.py still does) then left the
    tools calling the daemon with no API key and no session header.
    """
    from cli.tools import context, knowledge, retrieval, sessions, vault

    for mod in (context, knowledge, retrieval, sessions, vault):
        assert not hasattr(mod, "_auth_headers"), (
            f"{mod.__name__} binds _auth_headers by value; "
            "use cli.mcp_client._auth_headers at call time instead"
        )


def test_api_key_and_session_reach_the_daemon_call(tmp_path):
    """The end-to-end contract: headers set at startup arrive on the wire."""
    from cli import mcp_client
    from cli.tools.context import _memory_write_working

    try:
        mcp_client.set_auth_headers({"x-api-key": "sekret"})
        mcp_client.set_session_id(SESSION_ROW["id"])

        with patch("httpx.post") as mock_post:
            mock_post.return_value = MagicMock(
                raise_for_status=MagicMock(), json=MagicMock(return_value={})
            )
            _memory_write_working(
                {"filename": "insight.md", "content": "body", "vault_path": str(tmp_path)},
                "http://127.0.0.1:5051",
            )

        headers = mock_post.call_args[1]["headers"]
        assert headers["x-api-key"] == "sekret"
        assert headers[mcp_client.SESSION_HEADER] == SESSION_ROW["id"]
    finally:
        mcp_client.set_session_id(None)
        mcp_client.set_auth_headers({})


def test_session_id_is_attached_to_daemon_headers():
    from cli import mcp_client

    try:
        mcp_client.set_auth_headers({"x-api-key": "k"})
        mcp_client.set_session_id("abc-123")
        assert mcp_client._auth_headers[mcp_client.SESSION_HEADER] == "abc-123"

        mcp_client.set_session_id(None)
        assert mcp_client.SESSION_HEADER not in mcp_client._auth_headers
    finally:
        mcp_client.set_auth_headers({})


def test_write_working_attributes_to_the_registered_session(tmp_path):
    from cli import mcp_client
    from cli.tools.context import _memory_write_working

    try:
        mcp_client.set_auth_headers({})
        mcp_client.set_session_id(SESSION_ROW["id"])

        with patch("httpx.post") as mock_post:
            mock_post.return_value = MagicMock(
                raise_for_status=MagicMock(), json=MagicMock(return_value={})
            )
            result = _memory_write_working(
                {"filename": "insight.md", "content": "body", "vault_path": str(tmp_path)},
                "http://127.0.0.1:5051",
            )

        assert result["attributed_to_session"] == SESSION_ROW["id"]
        url = mock_post.call_args[0][0]
        assert url.endswith(f"/sessions/{SESSION_ROW['id']}/log")
        assert mock_post.call_args[1]["json"]["action"] == "created"
    finally:
        mcp_client.set_session_id(None)


def test_write_working_without_a_session_makes_no_daemon_call(tmp_path):
    from cli import mcp_client
    from cli.tools.context import _memory_write_working

    try:
        mcp_client.set_session_id(None)
        with patch("httpx.post") as mock_post:
            result = _memory_write_working(
                {"filename": "insight.md", "content": "body", "vault_path": str(tmp_path)},
                "http://127.0.0.1:5051",
            )
        assert result["attributed_to_session"] is None
        mock_post.assert_not_called()
    finally:
        mcp_client.set_session_id(None)
