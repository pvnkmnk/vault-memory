"""SQLite database backend implementation for lite mode."""

import asyncio
import json
import logging
import os
import re
import sqlite3
import threading
from typing import Any, Iterable
from contextlib import contextmanager
from pathlib import Path

from daemon.db_abstraction import DatabaseBackend

logger = logging.getLogger("vault-memoryd.sqlite")


# S30-6: Custom exception hierarchy for SQLite backend
class SqliteError(Exception):
    """Base exception for SQLite backend errors."""
    pass


class SqliteConnectionError(SqliteError):
    """Raised when SQLite connection cannot be established."""
    pass


class SqliteQueryError(SqliteError):
    """Raised when a SQLite query fails."""
    def __init__(self, message: str, query: str = "", params: tuple = ()):
        super().__init__(message)
        self.query = query
        self.params = params


class SqliteTranslationError(SqliteError):
    """Raised when SQL translation from Postgres to SQLite fails."""
    pass

_ANY_PATTERN = re.compile(
    r"(?P<expr>(?:\"[^\"]+\"|[A-Za-z_][\w$]*)(?:\.(?:\"[^\"]+\"|[A-Za-z_][\w$]*))?)\s*"
    r"(?P<op>=|!=|<>|LIKE)\s+ANY\s*\(\s*\?\s*\)",
    re.IGNORECASE,
)


def _normalize_param(value: Any) -> Any:
    return json.dumps(value) if isinstance(value, (list, dict)) else value


def _is_word_char(char: str) -> bool:
    return char.isalnum() or char == "_"


def _translate_sql(query: str) -> str:
    """Translate Postgres-style SQL to SQLite syntax without touching string literals."""
    out: list[str] = []
    i = 0
    in_single = False
    in_double = False
    in_line_comment = False
    in_block_comment = False

    while i < len(query):
        ch = query[i]
        next_ch = query[i + 1] if i + 1 < len(query) else ""

        if in_line_comment:
            out.append(ch)
            if ch == "\n":
                in_line_comment = False
            i += 1
            continue

        if in_block_comment:
            out.append(ch)
            if ch == "*" and next_ch == "/":
                out.append(next_ch)
                in_block_comment = False
                i += 2
            else:
                i += 1
            continue

        if not in_single and not in_double and ch == "-" and next_ch == "-":
            out.extend((ch, next_ch))
            in_line_comment = True
            i += 2
            continue

        if not in_single and not in_double and ch == "/" and next_ch == "*":
            out.extend((ch, next_ch))
            in_block_comment = True
            i += 2
            continue

        if ch == "'" and not in_double:
            out.append(ch)
            if in_single and next_ch == "'":
                out.append(next_ch)
                i += 2
                continue
            in_single = not in_single
            i += 1
            continue

        if ch == '"' and not in_single:
            out.append(ch)
            in_double = not in_double
            i += 1
            continue

        if not in_single and not in_double:
            if query.startswith("%s", i):
                out.append("?")
                i += 2
                continue

            if query[i : i + 5].upper() == "ILIKE":
                prev_char = query[i - 1] if i > 0 else " "
                next_char = query[i + 5] if i + 5 < len(query) else " "
                if not _is_word_char(prev_char) and not _is_word_char(next_char):
                    out.append("LIKE")
                    i += 5
                    continue

        out.append(ch)
        i += 1

    return "".join(out)


def _expand_any_clauses(query: str, params: Iterable[Any]) -> tuple[str, tuple[Any, ...]]:
    """
    Expand PostgreSQL ANY(?) clauses into SQLite-compatible IN/NOT IN/OR-LIKE clauses.
    """
    raw_params = list(params)
    consumed = 0
    out: list[str] = []
    expanded_params: list[Any] = []
    cursor = 0

    for match in _ANY_PATTERN.finditer(query):
        segment = query[cursor : match.start()]
        segment_placeholders = segment.count("?")
        out.append(segment)
        for _ in range(segment_placeholders):
            if consumed >= len(raw_params):
                raise SqliteTranslationError("Not enough SQL parameters provided.")
            expanded_params.append(_normalize_param(raw_params[consumed]))
            consumed += 1

        if consumed >= len(raw_params):
            raise SqliteTranslationError("Missing sequence parameter for ANY(...) clause.")

        any_param = raw_params[consumed]
        consumed += 1

        values = (
            list(any_param)
            if isinstance(any_param, (list, tuple, set))
            else [any_param]
        )

        expr = match.group("expr")
        operator = match.group("op").upper()

        if not values:
            out.append("0=1")
            cursor = match.end()
            continue

        placeholders = ", ".join("?" for _ in values)
        if operator == "=":
            out.append(f"{expr} IN ({placeholders})")
        elif operator in {"!=", "<>"}:
            out.append(f"{expr} NOT IN ({placeholders})")
        else:  # LIKE
            out.append("(" + " OR ".join(f"{expr} LIKE ?" for _ in values) + ")")

        expanded_params.extend(_normalize_param(value) for value in values)
        cursor = match.end()

    tail = query[cursor:]
    tail_placeholders = tail.count("?")
    out.append(tail)
    for _ in range(tail_placeholders):
        if consumed >= len(raw_params):
            raise SqliteTranslationError("Not enough SQL parameters provided.")
        expanded_params.append(_normalize_param(raw_params[consumed]))
        consumed += 1

    if consumed != len(raw_params):
        raise SqliteTranslationError("Too many SQL parameters provided.")

    return "".join(out), tuple(expanded_params)


class _SqliteCursorAdapter:
    """Adapt SQLite cursor behavior to the daemon's existing DB call sites."""

    def __init__(self, cursor: sqlite3.Cursor):
        self._cursor = cursor

    def execute(self, query: str, params: Iterable[Any] = ()) -> "_SqliteCursorAdapter":
        translated = _translate_sql(query)
        expanded_query, expanded_params = _expand_any_clauses(translated, tuple(params))
        self._cursor.execute(expanded_query, expanded_params)
        return self

    def executemany(
        self, query: str, seq_of_params: Iterable[Iterable[Any]]
    ) -> "_SqliteCursorAdapter":
        for params in seq_of_params:
            self.execute(query, params)
        return self

    def fetchone(self):
        return self._cursor.fetchone()

    def fetchall(self):
        return self._cursor.fetchall()

    @property
    def rowcount(self) -> int:
        return self._cursor.rowcount

    @property
    def lastrowid(self) -> int:
        return self._cursor.lastrowid

    def close(self) -> None:
        self._cursor.close()


class SqliteBackend(DatabaseBackend):
    """
    SQLite backend for lite mode.

    The broader daemon still expects synchronous `with db.cursor()` usage, so this
    backend exposes a synchronous cursor context manager while keeping async
    lifecycle methods for startup and health checks.
    """

    def __init__(
        self,
        db_path: str = None,
    ):
        if db_path is None:
            db_path = os.environ.get(
                "VAULT_MEMORY_DB_PATH",
                str(Path.home() / ".vault-memory" / "lite.db")
            )

        self.db_path = db_path
        self._db: sqlite3.Connection | None = None
        self._lock = threading.RLock()

        # Ensure directory exists
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)

        logger.info("SQLite backend initialized at: %s", db_path)

    async def pool(self) -> Any:
        """Return the connection (pool equivalent for SQLite)."""
        return self._db

    async def connect(self) -> None:
        """Establish connection to SQLite."""
        if self._db is None:
            await asyncio.to_thread(self._connect_and_init)

    def _connect_and_init(self) -> None:
        with self._lock:
            if self._db is not None:
                return
            self._db = sqlite3.connect(self.db_path, check_same_thread=False)
            self._db.row_factory = sqlite3.Row
            self._init_schema()

    async def disconnect(self) -> None:
        """Backward-compatible async close path."""
        self.close()

    def close(self) -> None:
        """Close connection."""
        with self._lock:
            if self._db:
                self._db.close()
                self._db = None

    def _init_schema(self) -> None:
        """Initialize SQLite schema for the lite-mode endpoints that remain supported."""
        if self._db is None:
            raise SqliteConnectionError("SQLite connection is not initialized")

        schema = """
        CREATE TABLE IF NOT EXISTS files (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            file_path TEXT UNIQUE NOT NULL,
            content TEXT,
            embedding BLOB,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS agent_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agent_name TEXT,
            project TEXT,
            task TEXT,
            vault_path TEXT,
            plan_ref TEXT,
            vault_paths TEXT,
            status TEXT DEFAULT 'active',
            started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            closed_at TIMESTAMP,
            notes TEXT
        );

        CREATE TABLE IF NOT EXISTS context_blocks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            file_path TEXT NOT NULL,
            tokens INTEGER,
            content TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS triples (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            subject TEXT NOT NULL,
            predicate TEXT NOT NULL,
            object TEXT NOT NULL,
            source_file TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE INDEX IF NOT EXISTS idx_files_path ON files(file_path);
        CREATE INDEX IF NOT EXISTS idx_triples_subject ON triples(subject);
        CREATE INDEX IF NOT EXISTS idx_triples_object ON triples(object);
        CREATE UNIQUE INDEX IF NOT EXISTS idx_triples_unique
            ON triples(subject, predicate, object);
        """
        self._db.executescript(schema)
        self._db.commit()

    @contextmanager
    def cursor(self):
        """Context manager for getting a cursor."""
        if self._db is None:
            raise SqliteConnectionError("SQLite connection not initialized")

        with self._lock:
            cursor = _SqliteCursorAdapter(self._db.cursor())
            try:
                yield cursor
                self._db.commit()
            except sqlite3.Error as e:
                self._db.rollback()
                logger.error("SQLite error: %s", e)
                raise SqliteQueryError(str(e)) from e
            except Exception as e:
                self._db.rollback()
                logger.error("SQLite error: %s", e)
                raise
            finally:
                cursor.close()

    async def execute(self, query: str, params: tuple = ()) -> list:
        """Execute a query and return results."""
        return await asyncio.to_thread(self._execute_sync, query, params)

    def _execute_sync(self, query: str, params: tuple = ()) -> list:
        with self.cursor() as cursor:
            cursor.execute(query, params)
            return cursor.fetchall()

    async def ping(self) -> bool:
        """Check if database is available."""
        try:
            if self._db is None:
                await self.connect()
            return await asyncio.to_thread(self._ping_sync)
        except Exception as e:
            logger.warning("SQLite health check failed: %s", e)
            return False

    def _ping_sync(self) -> bool:
        with self.cursor() as cursor:
            cursor.execute("SELECT 1")
        return True

    async def health_check(self) -> bool:
        """Backward-compatible alias for dependency checks."""
        return await self.ping()


# Backwards compatible alias
class SqliteClient(SqliteBackend):
    """Backwards compatible alias."""
    pass
