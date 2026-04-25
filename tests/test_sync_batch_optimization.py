import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from daemon import sync_watcher
from daemon.sync_watcher import SyncEngine


def _build_engine(vault_root: Path) -> tuple[SyncEngine, MagicMock, MagicMock]:
    weaviate = MagicMock()
    weaviate.batch_upsert = AsyncMock()
    weaviate.upsert_chunk = AsyncMock()
    weaviate.delete_by_path = AsyncMock()

    embedder = MagicMock()
    embedder.embed_batch = AsyncMock()
    embedder.embed_one = AsyncMock()

    pg = MagicMock()
    engine = SyncEngine(vault_root=vault_root, weaviate_client=weaviate, pg_client=pg, embedder=embedder)
    return engine, weaviate, embedder


def _normalize_path(value: str) -> str:
    return value.replace("\\", "/")


def test_sync_file_markdown_uses_single_batch_embed_and_upsert(monkeypatch, tmp_path):
    vault_root = tmp_path / "vault"
    md_path = vault_root / "Project" / "notes" / "note.md"
    md_path.parent.mkdir(parents=True)
    md_path.write_text(
        "---\n"
        "status: reviewing\n"
        "trust: medium\n"
        "maturity: tree\n"
        "---\n"
        "body content",
        encoding="utf-8",
    )

    chunks = ["first chunk", "second chunk", "third chunk"]
    monkeypatch.setattr(sync_watcher, "_chunk_text", lambda _: chunks)

    engine, weaviate, embedder = _build_engine(vault_root)
    embedder.embed_batch.return_value = [[0.11], [0.22], [0.33]]

    upserted = asyncio.run(engine.sync_file(md_path, caller="user"))

    assert upserted == 3
    embedder.embed_batch.assert_awaited_once_with(chunks)
    embedder.embed_one.assert_not_awaited()
    weaviate.batch_upsert.assert_awaited_once()
    weaviate.upsert_chunk.assert_not_awaited()

    pushed_chunks = weaviate.batch_upsert.await_args.args[0]
    assert len(pushed_chunks) == 3
    assert [_normalize_path(c.uuid) for c in pushed_chunks] == [
        "Project/notes/note.md::0",
        "Project/notes/note.md::1",
        "Project/notes/note.md::2",
    ]
    assert {_normalize_path(c.vault_path) for c in pushed_chunks} == {"Project/notes/note.md"}
    assert all(c.status == "reviewing" for c in pushed_chunks)
    assert all(c.trust == "medium" for c in pushed_chunks)
    assert all(c.maturity == "tree" for c in pushed_chunks)


def test_sync_canvas_uses_batch_embedding_and_batch_upsert(tmp_path):
    vault_root = tmp_path / "vault"
    canvas_path = vault_root / "Project" / "boards" / "map.canvas"
    canvas_path.parent.mkdir(parents=True)
    canvas_path.write_text(
        json.dumps(
            {
                "nodes": [
                    {"id": "n1", "text": "First"},
                    {"id": "n2", "text": "Second", "file": "Project/notes/note.md"},
                ],
                "edges": [
                    {"id": "e1", "fromNode": "n1", "toNode": "n2"},
                ],
            }
        ),
        encoding="utf-8",
    )

    engine, weaviate, embedder = _build_engine(vault_root)
    embedder.embed_batch.side_effect = [[[1.0], [2.0]], [[3.0]]]
    engine._upsert_entity_link = MagicMock()
    engine._upsert_relationship = MagicMock()

    upserted = asyncio.run(engine.sync_file(canvas_path, caller="user"))

    assert upserted == 3
    assert embedder.embed_batch.await_count == 2
    embedder.embed_one.assert_not_awaited()
    assert weaviate.batch_upsert.await_count == 2
    weaviate.upsert_chunk.assert_not_awaited()

    node_chunks = weaviate.batch_upsert.await_args_list[0].args[0]
    edge_chunks = weaviate.batch_upsert.await_args_list[1].args[0]
    assert len(node_chunks) == 2
    assert len(edge_chunks) == 1
    assert [_normalize_path(node.uuid) for node in node_chunks] == [
        "Project/boards/map.canvas::node::n1",
        "Project/boards/map.canvas::node::n2",
    ]
    assert _normalize_path(edge_chunks[0].uuid) == "Project/boards/map.canvas::edge::e1"
