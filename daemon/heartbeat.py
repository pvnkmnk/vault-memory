# daemon/heartbeat.py
"""
Heartbeat job with centrality recalc and topic hub refresh.
Background task that periodically:
1. Recalculates degree centrality for all temporal_entities
2. Refreshes topic_hubs table
3. Propagates centrality to sync_state
"""

import asyncio
import logging
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .pg_client import PostgresClient

logger = logging.getLogger("vault-memoryd.heartbeat")


async def recalc_centrality(postgres: PostgresClient) -> int:
    """
    Recalculate degree centrality for all entities.
    Centrality = degree(node) / (total_nodes - 1)
    Updates temporal_entities.centrality in place.
    Returns the number of entities updated.
    """
    try:
        with postgres.cursor() as cursor:
            # Count total entities for normalization
            cursor.execute("SELECT COUNT(*) AS total FROM temporal_entities")
            total = cursor.fetchone()["total"]
            if total <= 1:
                logger.debug("Centrality recalc skipped: %d entities", total)
                return 0

            # Degree centrality: count outgoing relationships per entity
            # Centrality is normalized by (total - 1)
            sql = """
            WITH degree_counts AS (
                SELECT
                    source_name AS entity_name,
                    COUNT(*) AS out_degree
                FROM relationships
                GROUP BY source_name
            ),
            all_entities AS (
                SELECT entity_name FROM temporal_entities
            ),
            merged AS (
                SELECT
                    ae.entity_name,
                    COALESCE(dc.out_degree, 0) AS degree
                FROM all_entities ae
                LEFT JOIN degree_counts dc ON ae.entity_name = dc.entity_name
            )
            UPDATE temporal_entities te
            SET centrality = (
                SELECT
                    CASE
                        WHEN %s <= 1 THEN 0.0
                        ELSE m.degree::FLOAT / (%s - 1)
                    END
                FROM merged m
                WHERE m.entity_name = te.entity_name
            )
            WHERE te.entity_name IN (SELECT entity_name FROM merged)
            """
            cursor.execute(sql, (total, total))
            updated = cursor.rowcount
            logger.info("Centrality recalc: updated %d entities, total=%d", updated, total)
            return updated
    except Exception as e:
        logger.error("Centrality recalc failed: %s", e)
        return 0


async def refresh_topic_hubs(postgres: PostgresClient, min_in_degree: int = 5) -> int:
    """
    Incrementally refresh the topic_hubs table (S24-P2 / VAU-16).

    Previous implementation TRUNCATEd the table every heartbeat cycle, which:
    - made `topic_hubs` briefly empty for concurrent /search_siblings readers
    - rewrote every row every 15 minutes even when nothing changed

    This version upserts qualifying hubs (INSERT ... ON CONFLICT DO UPDATE)
    and deletes only rows that no longer qualify, so readers never see an
    empty table mid-cycle.

    A vault file can link multiple entities, so the upsert source is
    deduplicated per vault_path (strongest entity wins — highest in-degree,
    ties broken alphabetically) — otherwise a single INSERT could propose the
    same conflict key twice and PostgreSQL would raise "ON CONFLICT DO UPDATE
    command cannot affect row a second time".

    Returns the number of hubs currently registered (a fresh COUNT, not a
    delta, since rowcount from the upsert counts updated rows too).
    """
    try:
        with postgres.cursor() as cursor:
            # Snapshot the pre-cycle hub count for the return value.
            cursor.execute(
                "SELECT COUNT(*)::int AS n, COALESCE(SUM(in_degree), 0)::int AS degree_sum FROM topic_hubs"
            )
            hub_row = cursor.fetchone()
            prev_count = hub_row["n"] or 0

            # Fast degenerate-graph bail-out: no edges and nothing recorded.
            cursor.execute("SELECT COUNT(*)::int AS n FROM relationships")
            if (cursor.fetchone()["n"] or 0) == 0:
                if prev_count == 0:
                    logger.debug("Topic hubs skipped: no relationships in graph")
                    return 0

            # Upsert every entity whose in-degree qualifies, deduplicated by
            # vault_path (one hub row per file; strongest entity represents it).
            cursor.execute(
                """
                INSERT INTO topic_hubs (vault_path, entity_name, in_degree, hub_penalty, last_updated)
                SELECT
                    ranked.vault_path,
                    ranked.entity_name,
                    ranked.in_degree,
                    1.0 / log(2.0, ranked.in_degree + 2) AS hub_penalty,
                    now()
                FROM (
                    SELECT
                        COALESCE(vel.vault_path, 'Unknown/' || d.entity_name || '.md') AS vault_path,
                        d.entity_name,
                        d.in_degree,
                        ROW_NUMBER() OVER (
                            PARTITION BY COALESCE(vel.vault_path, 'Unknown/' || d.entity_name || '.md')
                            ORDER BY d.in_degree DESC, d.entity_name ASC
                        ) AS rn
                    FROM (
                        SELECT target_name AS entity_name, COUNT(*) AS in_degree
                        FROM relationships
                        GROUP BY target_name
                        HAVING COUNT(*) >= %s
                    ) d
                    LEFT JOIN vault_entity_links vel
                        ON vel.entity_id::text = d.entity_name
                ) ranked
                WHERE ranked.rn = 1
                ON CONFLICT (vault_path) DO UPDATE SET
                    entity_name = EXCLUDED.entity_name,
                    in_degree = EXCLUDED.in_degree,
                    hub_penalty = EXCLUDED.hub_penalty,
                    last_updated = now()
                """,
                (min_in_degree,),
            )
            rows_written = cursor.rowcount

            # Remove only rows that fell below the threshold (or whose entity
            # no longer has any edges). Readers never observe an empty table.
            cursor.execute(
                """
                DELETE FROM topic_hubs th
                WHERE NOT EXISTS (
                    SELECT 1
                    FROM relationships r
                    WHERE r.target_name = th.entity_name
                    GROUP BY r.target_name
                    HAVING COUNT(*) >= %s
                )
                """,
                (min_in_degree,),
            )
            deleted = cursor.rowcount

            # Report the actual table size — upsert rowcount includes rows that
            # were merely updated, so prev + written - deleted would overcount.
            cursor.execute("SELECT COUNT(*)::int AS n FROM topic_hubs")
            current_count = cursor.fetchone()["n"] or 0

            if rows_written == 0 and deleted == 0:
                logger.debug(
                    "Topic hubs unchanged (hubs=%d, degree_sum=%d)", prev_count, hub_row["degree_sum"]
                )
            else:
                logger.info(
                    "Topic hubs refreshed: %d rows written, %d removed (min_in_degree=%d, total=%d)",
                    rows_written,
                    deleted,
                    min_in_degree,
                    current_count,
                )
            return current_count
    except Exception as e:
        logger.error("Topic hub refresh failed: %s", e)
        return 0


async def propagate_centrality_to_sync(postgres: PostgresClient) -> int:
    """
    Copy centrality values from temporal_entities to sync_state.centrality_score.
    This caches centrality at the file level for fast GARS scoring at search time.
    Returns number of rows updated.
    """
    try:
        with postgres.cursor() as cursor:
            # Update sync_state.centrality_score from the latest temporal_entities.centrality
            # for each file that has entity links
            sql = """
            WITH latest_entity AS (
                SELECT DISTINCT ON (vel.vault_path)
                    vel.vault_path,
                    te.centrality
                FROM vault_entity_links vel
                JOIN temporal_entities te
                    ON vel.entity_id = te.id
                ORDER BY vel.vault_path, te.centrality DESC
            )
            UPDATE sync_state ss
            SET centrality_score = le.centrality
            FROM latest_entity le
            WHERE ss.file_path = le.vault_path
            """
            cursor.execute(sql)
            updated = cursor.rowcount
            logger.info("Propagated centrality to %d sync_state rows", updated)
            return updated
    except Exception as e:
        logger.error("Centrality propagation failed: %s", e)
        return 0


class HeartbeatJob:
    """
    Background heartbeat job that runs on a configurable interval.
    Orchestrates centrality recalc, topic hub refresh, and sync propagation.
    """

    def __init__(
        self,
        postgres: PostgresClient,
        interval_seconds: int = 900,  # 15 minutes default
        vault_root: Optional[Path] = None,
    ):
        self.postgres = postgres
        self.interval_seconds = interval_seconds
        self.vault_root = Path(vault_root) if vault_root else None
        self._running = False
        self._task: Optional[asyncio.Task] = None

    def _miner_deps(self):
        """Minimal Dependencies stand-in for the miner and the digests.

        The heartbeat is constructed with a Postgres client only, while the
        miner and digest modules read ``deps.postgres`` / ``deps.settings``.
        """
        postgres = self.postgres

        class _Deps:
            def __init__(self) -> None:
                self.postgres = postgres
                self.settings = None

        return _Deps()

    async def _heartbeat_cycle(self) -> None:
        """Execute one full heartbeat cycle."""
        logger.info("Heartbeat cycle starting...")

        try:
            # Step 1: Recalculate centrality for all entities
            updated = await recalc_centrality(self.postgres)

            # Step 2: Refresh topic hubs based on new centrality
            hubs = await refresh_topic_hubs(self.postgres)

            # Step 3: Propagate centrality to sync_state cache
            propagated = await propagate_centrality_to_sync(self.postgres)

            # S28-1: Clean up stale sessions (run every cycle, defaults to 24h threshold)
            orphaned = await cleanup_stale_sessions(self.postgres)

            # S31-3: Drain the session-mining queue. Off unless SESSION_MINING=on,
            # because it needs an LLM and is expensive relative to the rest of the cycle.
            mined = 0
            if self.vault_root is not None:
                from daemon import miner

                if miner.mining_enabled():
                    summary = await miner.mine_once(
                        self._miner_deps(), self.vault_root, limit=3
                    )
                    mined = summary.get("mined", 0)

            # S32-2/3/4: the digest ladder. Off unless DIGESTS=on, and the
            # window triggers are computed from the clock, not run every cycle.
            digests = []
            if self.vault_root is not None:
                from daemon import digest as digest_module

                if digest_module.digest_enabled():
                    digests = await digest_module.run_due_digests(
                        self._miner_deps(), self.vault_root
                    )

            logger.info(
                "Heartbeat cycle complete: centrality=%d, hubs=%d, propagated=%d, "
                "orphaned=%d, mined=%d, digests=%d",
                updated,
                hubs,
                propagated,
                orphaned,
                mined,
                len(digests),
            )
        except Exception as e:
            logger.error("Heartbeat cycle failed: %s", e)

    async def _loop(self) -> None:
        """Main heartbeat loop."""
        while self._running:
            try:
                await self._heartbeat_cycle()
            except Exception as e:
                logger.error("Heartbeat loop error: %s", e)

            # Wait for next interval
            await asyncio.sleep(self.interval_seconds)

    async def start(self) -> None:
        """Start the heartbeat background task."""
        if self._running:
            logger.warning("Heartbeat already running")
            return
        self._running = True
        self._task = asyncio.create_task(self._loop())
        logger.info("Heartbeat started (interval=%ds)", self.interval_seconds)

    async def stop(self) -> None:
        """Stop the heartbeat background task."""
        if not self._running:
            return
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("Heartbeat stopped")

    async def run_once(self) -> None:
        """Run a single heartbeat cycle (useful for manual trigger)."""
        await self._heartbeat_cycle()


async def cleanup_stale_sessions(
    postgres, max_age_hours: int = 24
) -> int:
    """Mark sessions as orphaned if they've been inactive for max_age_hours.

    S28-1: Stale session cleanup for agent_sessions table.
    """
    if not hasattr(postgres, 'cursor') or not callable(postgres.cursor):
        return 0
    try:
        def _do_cleanup():
            with postgres.cursor() as cur:
                cur.execute(
                    '''
                    UPDATE agent_sessions
                    SET status = 'closed', closed_at = now()
                    WHERE status = 'active'
                      AND started_at < now() - (%s || ' hours')::interval
                      AND last_ping_at < now() - (%s || ' hours')::interval
                    ''',
                    (max_age_hours, max_age_hours),
                )
                return cur.rowcount
        return await asyncio.to_thread(_do_cleanup)
    except Exception as e:
        logger.debug('cleanup_stale_sessions skipped: %s', e)
        return 0


class HeartbeatService:
    """
    Heartbeat service wrapper for main.py integration.
    Takes interval_seconds and manages HeartbeatJob lifecycle.
    """

    def __init__(self, interval_seconds: int = 900):
        self.interval_seconds = interval_seconds
        self._job: Optional[HeartbeatJob] = None

    async def start(
        self, postgres: PostgresClient, vault_root: Optional[Path] = None
    ) -> None:
        """Start the heartbeat with postgres client."""
        self._job = HeartbeatJob(postgres, self.interval_seconds, vault_root=vault_root)
        await self._job.start()

    async def stop(self) -> None:
        """Stop the heartbeat."""
        if self._job:
            await self._job.stop()
