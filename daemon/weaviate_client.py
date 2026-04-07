# daemon/weaviate_client.py
import logging
from typing import List
import weaviate
from weaviate.classes.config import Configure, Property, DataType
from weaviate.util import generate_uuid5

logger = logging.getLogger("vault-memoryd.weaviate")
COLLECTION = "VaultNote"


class WeaviateClient:
    def __init__(self, url: str):
        host = url.replace("http://", "").replace("https://", "").split(":")[0]
        port = int(url.split(":")[-1]) if ":" in url else 8080
        self.client = weaviate.connect_to_local(host=host, port=port)
        self._ensure_schema()

    def _ensure_schema(self):
        if self.client.collections.exists(COLLECTION):
            return
        self.client.collections.create(
            name=COLLECTION,
            vectorizer_config=Configure.Vectorizer.none(),
            properties=[
                Property(name="content",       data_type=DataType.TEXT),
                Property(name="vault_path",    data_type=DataType.TEXT),
                Property(name="project",       data_type=DataType.TEXT),
                Property(name="folder",        data_type=DataType.TEXT),
                Property(name="tags",          data_type=DataType.TEXT_ARRAY),
                Property(name="date_created",  data_type=DataType.DATE),
                Property(name="date_modified", data_type=DataType.DATE),
                Property(name="status",        data_type=DataType.TEXT),
                Property(name="chunk_index",   data_type=DataType.INT),
                Property(name="chunk_total",   data_type=DataType.INT),
                Property(name="content_hash",  data_type=DataType.TEXT),
            ],
        )
        logger.info("Created Weaviate collection: %s", COLLECTION)

    async def batch_upsert(self, chunks):
        collection = self.client.collections.get(COLLECTION)
        with collection.batch.dynamic() as batch:
            for chunk in chunks:
                uuid = generate_uuid5(f"{chunk.vault_path}::{chunk.chunk_index}")
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
                    },
                    vector=chunk.embedding,
                    uuid=uuid,
                )

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
