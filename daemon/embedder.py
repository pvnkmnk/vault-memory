# daemon/embedder.py
"""
Embedding + reranker service.
Loaded once in daemon startup; reused by every sync and search.
"""

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

    def embed_batch(self, texts: List[str]) -> List[List[float]]:
        """Embed a list of texts. Returns list of embedding vectors."""
        vectors = self.embedder.encode(texts, batch_size=32, show_progress_bar=False)
        return vectors.tolist()

    def embed_one(self, text: str) -> List[float]:
        return self.embedder.encode([text])[0].tolist()

    def rerank(self, query: str, candidates: List[str]) -> List[float]:
        pairs = [[query, c] for c in candidates]
        return self.reranker.predict(pairs).tolist()
