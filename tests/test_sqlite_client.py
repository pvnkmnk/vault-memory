import asyncio
from types import SimpleNamespace

from daemon.backends.sqlite_client import SqliteBackend, _translate_sql
from daemon.main import _persist_cognify_triples


def test_translate_sql_keeps_literals_intact():
    query = "SELECT '%s' AS marker, col ILIKE %s, \"ILIKE\" AS quoted_identifier"
    translated = _translate_sql(query)
    assert translated == "SELECT '%s' AS marker, col LIKE ?, \"ILIKE\" AS quoted_identifier"


def test_sqlite_cursor_expands_any_and_like_any(tmp_path):
    backend = SqliteBackend(str(tmp_path / "lite.db"))
    asyncio.run(backend.connect())

    try:
        with backend.cursor() as cursor:
            cursor.execute("CREATE TABLE rel (source TEXT NOT NULL, target TEXT NOT NULL)")
            cursor.executemany(
                "INSERT INTO rel (source, target) VALUES (%s, %s)",
                [("alpha", "x"), ("beta", "y"), ("gamma", "x")],
            )

            cursor.execute(
                "SELECT source FROM rel WHERE source = ANY(%s) ORDER BY source",
                (["beta", "gamma"],),
            )
            assert [row[0] for row in cursor.fetchall()] == ["beta", "gamma"]

            cursor.execute(
                "SELECT source FROM rel WHERE source ILIKE ANY(%s) ORDER BY source",
                (["a%", "g%"],),
            )
            assert [row[0] for row in cursor.fetchall()] == ["alpha", "gamma"]
    finally:
        backend.close()


def test_persist_cognify_triples_uses_sqlite_triples_table_in_lite_mode(tmp_path):
    backend = SqliteBackend(str(tmp_path / "lite.db"))
    asyncio.run(backend.connect())

    deps = SimpleNamespace(
        settings=SimpleNamespace(lite_mode=True),
        postgres=backend,
    )

    triples = [
        {"subject": "Alice", "predicate": "mentions", "object": "Bob"},
        {"subject": "Alice", "predicate": "mentions", "object": "Bob"},
    ]

    try:
        result = _persist_cognify_triples(triples, deps)
        assert result["persisted"] is True
        assert result["relationships_written"] == 1
        assert result["entities_written"] == 2

        with backend.cursor() as cursor:
            cursor.execute("SELECT subject, predicate, object FROM triples ORDER BY id")
            rows = cursor.fetchall()

        assert len(rows) == 1
        assert rows[0]["subject"] == "Alice"
        assert rows[0]["predicate"] == "MENTIONS"
        assert rows[0]["object"] == "Bob"
    finally:
        backend.close()
