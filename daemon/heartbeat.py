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
    Rebuild the topic_hubs table based on current relationship in-degrees.
    A topic hub qualifies when in-degree >= min_in_degree.
    Hub penalty = 1 / log2(in_degree + 2)
    Returns number of hubs registered.
    """
    try:
        with postgres.cursor() as cursor:
            # First, clear existing hubs
            cursor.execute("TRUNCATE topic_hubs")

            # Compute in-degree for each target entity and register qualifying hubs
            sql = """
            WITH in_degrees AS (
                SELECT
                    target_name AS entity_name,
                    COUNT(*) AS in_degree
                FROM relationships
                GROUP BY target_name
                HAVING COUNT(*) >= %s
            )
            INSERT INTO topic_hubs (vault_path, entity_name, in_degree, hub_penalty, last_updated)
            SELECT
                COALESCE(vel.vault_path, 'Unknown/' || id.entity_name || '.md') AS vault_path,
                id.entity_name,
                id.in_degree,
                1.0 / log(2.0, id.in_degree + 2) AS hub_penalty,
                now()
            FROM in_degrees id
            LEFT JOIN vault_entity_links vel
                ON vel.entity_id::text = id.entity_name
            """
            cursor.execute(sql, (min_in_degree,))
            count = cursor.rowcount
            logger.info(
                "Topic hubs refreshed: %d hubs registered (min_in_degree=%d)", count, min_in_degree
            )
            return count
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
    ):
        self.postgres = postgres
        self.interval_seconds = interval_seconds
        self._running = False
        self._task: Optional[asyncio.Task] = None

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

            logger.info(
                "Heartbeat cycle complete: centrality=%d, hubs=%d, propagated=%d, orphaned=%d",
                updated,
                hubs,
                propagated,
                orphaned,
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
                    SET status = 'closed', closed_at = COALESCE(closed_at, now())
                    WHERE status = 'active'
                      AND COALESCE(last_ping_at, started_at)
                          < now() - (%s || ' hours')::interval
                    ''',
                    (max_age_hours,),
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

    async def start(self, postgres: PostgresClient) -> None:
        """Start the heartbeat with postgres client."""
        self._job = HeartbeatJob(postgres, self.interval_seconds)
        await self._job.start()

    async def stop(self) -> None:
        """Stop the heartbeat."""
        if self._job:
            await self._job.stop()
