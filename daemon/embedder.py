# daemon/embedder.py
import asyncio
import logging
from typing import List
from sentence_transformers import SentenceTransformer, CrossEncoder

logger = logging.getLogger("vault-memoryd.embedder")


class EmbedderService:
    def __init__(self, embedding_model: str, reranker_model: str):
        logger.info("Loading embedding model: %s", embedding_model)
        self.embedder = SentenceTransformer(embedding_model)
        logger.info("Loading reranker model: %s", reranker_model)
        self.reranker = CrossEncoder(reranker_model)

    # Sync methods (for backward compatibility)
    def embed_batch_sync(self, texts: List[str]) -> List[List[float]]:
        vectors = self.embedder.encode(texts, batch_size=32, show_progress_bar=False)
        return vectors.tolist()

    def embed_one_sync(self, text: str) -> List[float]:
        return self.embedder.encode([text])[0].tolist()

    def rerank_sync(self, query: str, candidates: List[str]) -> List[float]:
        pairs = [[query, c] for c in candidates]
        return self.reranker.predict(pairs).tolist()

    # Async methods (run in executor to avoid blocking event loop)
    async def embed_batch(self, texts: List[str]) -> List[List[float]]:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self.embed_batch_sync, texts)

    async def embed_one(self, text: str) -> List[float]:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self.embed_one_sync, text)

    async def rerank(self, query: str, candidates: List[str]) -> List[float]:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self.rerank_sync, query, candidates)
