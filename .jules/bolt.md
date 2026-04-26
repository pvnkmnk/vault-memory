## 2026-04-16 - [Batch Embedding during Sync]
**Learning:** Sequential calls to `embed_one` during file sync create unnecessary overhead by repeatedly offloading to the thread pool and failing to leverage `sentence-transformers` internal batching optimizations.
**Action:** Transition `sync_file` and `_sync_canvas` to use `embed_batch` and `batch_upsert` to significantly reduce indexing time for multi-chunk files and canvas files.

## 2025-05-15 - [Search Pipeline Latency]
**Learning:** The search pipeline in `UnifiedSearch.search` was awaiting the query embedding before starting other search strategies (sparse, graph, temporal), even though those strategies do not depend on the embedding. Additionally, several strategies perform blocking I/O on the main event loop.
**Action:** Parallelize the embedding calculation with non-dependent search strategies. Use `asyncio.to_thread` for blocking I/O operations (like ripgrep) to avoid stalling the event loop. Combine multiple related database queries (e.g., in GARS rescoring) to reduce round-trip overhead.