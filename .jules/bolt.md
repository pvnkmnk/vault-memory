## 2026-04-16 - [Batch Embedding during Sync]
**Learning:** Sequential calls to `embed_one` during file sync create unnecessary overhead by repeatedly offloading to the thread pool and failing to leverage `sentence-transformers` internal batching optimizations.
**Action:** Transition `sync_file` and `_sync_canvas` to use `embed_batch` and `batch_upsert` to significantly reduce indexing time for multi-chunk files and canvas files.

## 2025-05-15 - [Search Pipeline Latency]
**Learning:** The search pipeline in `UnifiedSearch.search` was awaiting the query embedding before starting other search strategies (sparse, graph, temporal), even though those strategies do not depend on the embedding. Additionally, several strategies perform blocking I/O on the main event loop.
**Action:** Parallelize the embedding calculation with non-dependent search strategies. Use `asyncio.to_thread` for blocking I/O operations (like ripgrep) to avoid stalling the event loop. Combine multiple related database queries (e.g., in GARS rescoring) to reduce round-trip overhead.

## 2025-05-16 - [Batch Database Writes and Non-blocking Retrieval]
**Learning:** Performing individual PostgreSQL `INSERT` calls during canvas synchronization (one per node/edge) and executing synchronous GARS stats queries on the main event loop created significant bottlenecks during indexing and search.
**Action:** Use `psycopg2.extras.execute_values` for bulk inserts in `SyncEngine` and wrap blocking DB calls in `asyncio.to_thread` within `UnifiedSearch._apply_gars` to maintain event loop responsiveness.

## 2025-05-17 - [SQL Conflict Target Precision]
**Learning:** When refactoring individual SQL `INSERT`s into `psycopg2.extras.execute_values` batch calls, the `ON CONFLICT` target must exactly match an existing unique index or constraint. Mismatching the target (e.g., using 4 columns when the index is on 2, or vice versa) causes immediate runtime failures.
**Action:** Always verify the database schema or existing code's conflict target before implementing batch upserts. For the `relationships` table, the unique constraint is `(source_name, target_name, relationship_type, edge_source)` — all bulk inserts (wiki-links and canvas) must target this full 4-column composite key.

## 2025-05-18 - [Chunking Redundancy and SQL Argument Order]
**Learning:** The `_chunk_text` function was performing a redundant `split()` on every chunk to verify its word count, which is O(N) relative to chunk size. Additionally, `psycopg2.extras.execute_values` requires positional arguments (cursor, sql, argslist) to precede keyword arguments; violating this causes a `SyntaxError` that blocks batch synchronization.
**Action:** Use word slice index offsets `(end - i)` to determine word count during chunking. Always place `page_size` and other keyword arguments after positional arguments in `execute_values` calls to ensure batching stability.