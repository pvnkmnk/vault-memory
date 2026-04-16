# daemon/weaviate_client.py
import logging
import asyncio
import weaviate
from weaviate.classes.config import Configure, Property, DataType
from weaviate.util import generate_uuid5

logger = logging.getLogger("vault-memoryd.weaviate")
COLLECTION = "VaultNote"


class WeaviateClient:
    def __init__(self, url: str):
        host = url.replace("http://", "").replace("https://", "").split(":")[0]
        port = int(url.split(":")[-1]) if ":" in url else 8080
        self.client = weaviate.connect_to_custom(
            http_host=host,
            http_port=port,
            grpc_host=host,
            grpc_port=50051,
            skip_init_checks=False,
        )
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
        await asyncio.to_thread(self._batch_upsert_sync, chunks)

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
        collection = self.client.collections.get(COLLECTION)
        collection.data.delete_many(
            where=Filter.by_property("vault_path").equal(vault_path)
        )

    async def ping(self):
        if not self.client.is_ready():
            raise RuntimeError("Weaviate not ready")

    def close(self):
        self.client.close()
