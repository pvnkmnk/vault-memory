import asyncio
import pytest
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock, patch
from daemon.sync_watcher import SyncEngine, NoteChunk, CanvasNode, CanvasEdge

@pytest.fixture
def mock_deps():
    weaviate = MagicMock()
    weaviate.batch_upsert = AsyncMock()
    pg = MagicMock()
    embedder = MagicMock()
    embedder.embed_batch = AsyncMock(side_effect=lambda texts: [[0.1]*384 for _ in texts])
    return weaviate, pg, embedder

@pytest.mark.asyncio
async def test_sync_file_batching(tmp_path, mock_deps):
    weaviate, pg, embedder = mock_deps
    vault_root = tmp_path / "vault"
    vault_root.mkdir()

    # Create a markdown file that will result in multiple chunks
    md_file = vault_root / "test.md"
    # CHUNK_SIZE_TOKENS = 512, WORDS_PER_TOKEN = 0.75 -> ~384 words per chunk
    # Let's create ~1000 words to ensure at least 2-3 chunks
    md_file.write_text("word " * 1000)

    engine = SyncEngine(vault_root, weaviate, pg, embedder)

    with patch("daemon.sync_watcher._chunk_text", return_value=["chunk1", "chunk2", "chunk3"]):
        await engine.sync_file(md_file)

    # Verify embed_batch was called once with all chunks
    embedder.embed_batch.assert_called_once_with(["chunk1", "chunk2", "chunk3"])

    # Verify batch_upsert was called once with all NoteChunk objects
    weaviate.batch_upsert.assert_called_once()
    args, _ = weaviate.batch_upsert.call_args
    chunks = args[0]
    assert len(chunks) == 3
    assert all(isinstance(c, NoteChunk) for c in chunks)

@pytest.mark.asyncio
async def test_sync_canvas_batching(tmp_path, mock_deps):
    weaviate, pg, embedder = mock_deps
    vault_root = tmp_path / "vault"
    vault_root.mkdir()

    canvas_file = vault_root / "test.canvas"
    canvas_file.write_text('{"nodes": [{"id": "n1", "text": "node1"}, {"id": "n2", "text": "node2"}], "edges": [{"id": "e1", "fromNode": "n1", "toNode": "n2"}]}')

    engine = SyncEngine(vault_root, weaviate, pg, embedder)

    await engine._sync_canvas(canvas_file, "test.canvas")

    # 2 nodes + 1 edge = 3 items
    embedder.embed_batch.assert_called_once()
    args_embed, _ = embedder.embed_batch.call_args
    assert len(args_embed[0]) == 3

    weaviate.batch_upsert.assert_called_once()
    args_upsert, _ = weaviate.batch_upsert.call_args
    all_items = args_upsert[0]
    assert len(all_items) == 3
    assert any(isinstance(c, CanvasNode) for c in all_items)
    assert any(isinstance(c, CanvasEdge) for c in all_items)
