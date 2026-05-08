# daemon/weaviate_client.py
# S20-D: Parallel Weaviate batch ingestion

import logging
import asyncio
from asyncio import Semaphore
from typing import TYPE_CHECKING, Optional
import weaviate
from weaviate.classes.config import Configure, Property, DataType
from weaviate.util import generate_uuid5

if TYPE_CHECKING:
    from .circuit_breaker import CircuitBreaker

logger = logging.getLogger(__name__)
COLLECTION = "VaultNote"

# Default batch concurrency (S20-D)
DEFAULT_BATCH_CONCURRENCY = 5
# Chunks per batch for optimal Weaviate throughput
WEAVIATE_BATCH_SIZE = 100


class WeaviateClient:
    def __init__(self, url: str, batch_concurrency: int = DEFAULT_BATCH_CONCURRENCY, circuit_breaker: Optional['CircuitBreaker'] = None):
        host = url.replace("http://", "").replace("https://", "").split(":")[0]
        port = int(url.split(":")[-1]) if ":" in url else 8080
        self.client = weaviate.connect_to_custom(
            http_host=host,
            http_port=port,
            grpc_host=host,
            grpc_port=50051,
            skip_init_checks=False,
        )
        # S20-D: Semaphore for controlling concurrent batch operations
        self._batch_semaphore = Semaphore(max(1, min(batch_concurrency, 20)))  # Clamp 1-20
        # S30-4: Circuit breaker for external service protection
        self._circuit_breaker = circuit_breaker
        self._ensure_schema()

    def _ensure_schema(self):
        properties = [
            Property(name="content", data_type=DataType.TEXT),
            Property(name="vault_path", data_type=DataType.TEXT),
            Property(name="project", data_type=DataType.TEXT),
            Property(name="folder", data_type=DataType.TEXT),
            Property(name="tags", data_type=DataType.TEXT_ARRAY),
            Property(name="date_created", data_type=DataType.DATE),
            Property(name="date_modified", data_type=DataType.DATE),
            Property(name="status", data_type=DataType.TEXT),
            Property(name="chunk_index", data_type=DataType.INT),
            Property(name="chunk_total", data_type=DataType.INT),
            Property(name="content_hash", data_type=DataType.TEXT),
            Property(name="importance", data_type=DataType.NUMBER),
            Property(name="trust", data_type=DataType.TEXT),
            Property(name="maturity", data_type=DataType.TEXT),
            Property(name="decay_profile", data_type=DataType.TEXT),
            Property(name="agent_written", data_type=DataType.BOOL),
        ]
        if not self.client.collections.exists(COLLECTION):
            self.client.collections.create(
                name=COLLECTION,
                vectorizer_config=Configure.Vectorizer.none(),
                properties=properties,
            )
            logger.info("Created Weaviate collection: %s", COLLECTION)
            return

        existing = self.client.collections.get(COLLECTION)
        existing_config = existing.config.get()
        existing_props = {p.name for p in existing_config.properties}
        for prop in properties:
            if prop.name not in existing_props:
                existing.config.add_property(prop)
                logger.info("Added missing Weaviate property: %s", prop.name)

    async def batch_upsert(self, chunks):
        """
        S20-D: Parallel batch upsert with semaphore-controlled concurrency.
        Splits large batches into chunks of WEAVIATE_BATCH_SIZE for optimal throughput.
        """
        if not chunks:
            return

        # Split into smaller batches for Weaviate optimal processing
        batch_chunks = [
            chunks[i:i + WEAVIATE_BATCH_SIZE]
            for i in range(0, len(chunks), WEAVIATE_BATCH_SIZE)
        ]

        async def process_batch(batch):
            async with self._batch_semaphore:
                await asyncio.to_thread(self._batch_upsert_sync, batch)

        async def _do_upsert():
            # Process all batches in parallel with semaphore limiting concurrency
            await asyncio.gather(*[process_batch(b) for b in batch_chunks])

        if self._circuit_breaker:
            await self._circuit_breaker.execute(_do_upsert)
        else:
            await _do_upsert()

    def _batch_upsert_sync(self, chunks):
        collection = self.client.collections.get(COLLECTION)
        with collection.batch.dynamic() as batch:
            for chunk in chunks:
                uuid = generate_uuid5(chunk.uuid)
                batch.add_object(
                    properties={
                        "content":       chunk.content,
                        "vault_path":    chunk.vault_path,
                        "project":       chunk.project,
                        "folder":        chunk.folder,
                        "tags":          chunk.tags,
                        "date_created":  chunk.date_created,
                        "date_modified": chunk.date_modified,
                        "status":        chunk.status,
                        "chunk_index":   chunk.chunk_index,
                        "chunk_total":   chunk.chunk_total,
                        "content_hash":  chunk.content_hash,
                        "importance":    getattr(chunk, "importance", 1.0),
                        "trust":         getattr(chunk, "trust", "high"),
                        "maturity":      getattr(chunk, "maturity", "seed"),
                        "decay_profile": getattr(chunk, "decay_profile", "active"),
                        "agent_written": getattr(chunk, "agent_written", False),
                    },
                    vector=chunk.embedding,
                    uuid=uuid,
                )
        if getattr(batch, "failed_objects", None):
            raise RuntimeError(f"Weaviate batch upsert failed objects: {len(batch.failed_objects)}")

    async def upsert_chunk(self, chunk):
        await self.batch_upsert([chunk])

    async def delete_by_path(self, vault_path: str):
        from weaviate.classes.query import Filter
        async def _do_delete():
            collection = self.client.collections.get(COLLECTION)
            collection.data.delete_many(
                where=Filter.by_property("vault_path").equal(vault_path)
            )
        if self._circuit_breaker:
            await self._circuit_breaker.execute(_do_delete)
        else:
            await _do_delete()

    async def ping(self):
        async def _do_ping():
            if not self.client.is_ready():
                raise RuntimeError("Weaviate not ready")
        if self._circuit_breaker:
            await self._circuit_breaker.execute(_do_ping)
        else:
            await _do_ping()

    def close(self):
        self.client.close()
