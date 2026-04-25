# daemon/embedder.py
import asyncio
import logging
import math
import subprocess
from typing import List, Optional
from sentence_transformers import SentenceTransformer, CrossEncoder

logger = logging.getLogger(__name__)


def _detect_gpu_memory() -> Optional[int]:
    '''
    Detect GPU memory in bytes. Returns None if no GPU available.
    Uses torch.cuda if available, otherwise checks nvidia-smi.
    '''
    try:
        import torch
        if torch.cuda.is_available():
            # Get memory for the first GPU (index 0)
            total = torch.cuda.get_device_properties(0).total_memory
            logger.info("GPU detected: %.1fGB", total / (1024**3))
            return total
    except ImportError:
        pass
    
    # Fallback: try nvidia-smi
    try:
        result = subprocess.run(
            ['nvidia-smi', '--query-gpu=memory.total', '--format=csv,noheader,nounits'],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            memory_mb = int(result.stdout.strip().split('\n')[0])
            memory_bytes = memory_mb * 1024 * 1024
            logger.info("GPU detected via nvidia-smi: %dMB", memory_mb)
            return memory_bytes
    except (FileNotFoundError, subprocess.TimeoutExpired, ValueError):
        pass
    
    return None


def _calculate_optimal_batch_size(gpu_memory_bytes: Optional[int], config_batch_size: Optional[int] = None) -> int:
    '''
    Calculate optimal batch size based on available GPU memory.
    
    Strategy:
    - If config batch size provided and valid, use it (user override)
    - If GPU available, use larger batches (128-256 based on memory)
    - If CPU only, use smaller batch size (16) for memory efficiency
    
    Rule of thumb: ~200MB per batch item for e5-large model at 1024 tokens
    We target using at most 25% of GPU memory for embeddings to leave room
    for model weights and other operations.
    '''
    # User override takes precedence
    if config_batch_size is not None and config_batch_size > 0:
        logger.info("Using user-configured batch size: %d", config_batch_size)
        return config_batch_size
    
    # Default batch sizes
    CPU_BATCH_SIZE = 16
    GPU_MEMORY_PER_ITEM = 200 * 1024 * 1024  # ~200MB per item estimate
    TARGET_MEMORY_FRACTION = 0.25
    
    if gpu_memory_bytes is None:
        logger.info("No GPU detected, using CPU batch size: %d", CPU_BATCH_SIZE)
        return CPU_BATCH_SIZE
    
    # Calculate based on available GPU memory
    available_memory = gpu_memory_bytes * TARGET_MEMORY_FRACTION
    max_items = int(available_memory / GPU_MEMORY_PER_ITEM)
    
    # Clamp to reasonable bounds
    batch_size = max(16, min(max_items, 256))
    
    # Round down to nearest power of 2 for better memory alignment
    batch_size = 2 ** int(math.log2(batch_size))
    
    logger.info("GPU batch size calculated: %d (based on %.1fGB GPU memory)", batch_size, gpu_memory_bytes / (1024**3))
    return batch_size


class EmbedderService:
    def __init__(self, embedding_model: str, reranker_model: str, embed_batch_size: Optional[int] = None):
        logger.info("Loading embedding model: %s", embedding_model)
        self.embedder = SentenceTransformer(embedding_model)
        logger.info("Loading reranker model: %s", reranker_model)
        self.reranker = CrossEncoder(reranker_model)
        
        # Determine GPU availability and optimal batch size
        self._gpu_memory = _detect_gpu_memory()
        self._batch_size = _calculate_optimal_batch_size(self._gpu_memory, embed_batch_size)
        
        gpu_desc = self._gpu_memory or 'CPU only'
        logger.info("Embedder initialized with batch_size=%d, gpu_memory=%s", self._batch_size, gpu_desc)

    def _embed_batch(self, texts: List[str]) -> List[List[float]]:
        vectors = self.embedder.encode(texts, batch_size=self._batch_size, show_progress_bar=False)
        return vectors.tolist()

    def _embed_one(self, text: str) -> List[float]:
        return self.embedder.encode([text], batch_size=self._batch_size)[0].tolist()

    def _rerank(self, query: str, candidates: List[str]) -> List[float]:
        pairs = [[query, c] for c in candidates]
        return self.reranker.predict(pairs).tolist()

    # Async methods (run in executor to avoid blocking event loop)
    async def embed_batch(self, texts: List[str]) -> List[List[float]]:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._embed_batch, texts)

    async def embed_one(self, text: str) -> List[float]:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._embed_one, text)

    async def rerank(self, query: str, candidates: List[str]) -> List[float]:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._rerank, query, candidates)
    
    @property
    def batch_size(self) -> int:
        '''Current effective batch size.'''
        return self._batch_size
    
    @property
    def has_gpu(self) -> bool:
        '''Whether GPU acceleration is available.'''
        return self._gpu_memory is not None
    
    @property
    def gpu_memory_bytes(self) -> Optional[int]:
        '''GPU memory in bytes, or None if CPU only.'''
        return self._gpu_memory
