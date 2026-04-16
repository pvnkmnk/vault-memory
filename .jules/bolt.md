## 2026-04-16 - [Batch Embedding during Sync]
**Learning:** Sequential calls to `embed_one` during file sync create unnecessary overhead by repeatedly offloading to the thread pool and failing to leverage `sentence-transformers` internal batching optimizations.
**Action:** Transition `sync_file` and `_sync_canvas` to use `embed_batch` and `batch_upsert` to significantly reduce indexing time for multi-chunk files and canvas files.
