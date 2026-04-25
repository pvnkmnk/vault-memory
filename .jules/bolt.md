## 2025-05-15 - [Search Pipeline Latency]
**Learning:** The search pipeline in `UnifiedSearch.search` was awaiting the query embedding before starting other search strategies (sparse, graph, temporal), even though those strategies do not depend on the embedding. Additionally, several strategies perform blocking I/O on the main event loop.
**Action:** Parallelize the embedding calculation with non-dependent search strategies. Use `asyncio.to_thread` for blocking I/O operations (like ripgrep) to avoid stalling the event loop. Combine multiple related database queries (e.g., in GARS rescoring) to reduce round-trip overhead.

## 2026-04-25 - [Agent Ritual Consolidation]
**Learning:** Consolidating ad-hoc scheduled tasks into well-defined Agent Rituals with specific personas (Sentinel, Conductor, Bridge) improves the "Elite" status of the repository by providing clear ownership and repeatable quality patterns.
**Action:** Define personas in `.jules/agents/` and a master schedule in `.jules/tasks/weekly_schedule.md`.
