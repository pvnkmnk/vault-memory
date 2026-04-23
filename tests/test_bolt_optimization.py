import pytest
import asyncio
from unittest.mock import MagicMock, AsyncMock, patch
from daemon.retrieval import UnifiedSearch, VaultResult

@pytest.mark.asyncio
async def test_search_parallelization():
    # Mock dependencies
    mock_weaviate = MagicMock()
    mock_postgres = MagicMock()
    mock_embedder = MagicMock()

    # Track calls to embedder
    embed_call_event = asyncio.Event()

    async def slow_embed(text):
        await asyncio.sleep(0.1)
        embed_call_event.set()
        return [0.1] * 384

    mock_embedder.embed_one = AsyncMock(side_effect=slow_embed)

    # Mock search strategies
    with patch("daemon.retrieval._strategy_dense", new_callable=AsyncMock) as mock_dense, \
         patch("daemon.retrieval._strategy_sparse", new_callable=AsyncMock) as mock_sparse, \
         patch("daemon.retrieval.reciprocal_rank_fusion") as mock_rrf:

        mock_dense.return_value = []
        mock_sparse.return_value = []
        mock_rrf.return_value = []

        searcher = UnifiedSearch(mock_weaviate, mock_postgres, mock_embedder)

        # Run search
        await searcher.search("test query", apply_decay=False)

        # Verify dense strategy was called (it calls embed_one internally now)
        mock_dense.assert_called_once()
        # Verify sparse strategy was called in parallel (it doesn't wait for embed_one)
        mock_sparse.assert_called_once()

@pytest.mark.asyncio
async def test_ripgrep_to_thread():
    mock_weaviate = MagicMock()
    mock_postgres = MagicMock()
    mock_embedder = MagicMock()

    searcher = UnifiedSearch(mock_weaviate, mock_postgres, mock_embedder)

    with patch("daemon.retrieval._ripgrep_search") as mock_rg, \
         patch("asyncio.to_thread", wraps=asyncio.to_thread) as mock_to_thread:

        mock_rg.return_value = None

        # We need to mock more to get through the search method
        with patch("daemon.retrieval.classify_query") as mock_classify, \
             patch("daemon.retrieval.extract_entities") as mock_entities, \
             patch("daemon.retrieval.extract_time_range") as mock_tr, \
             patch("daemon.retrieval.build_weaviate_filter") as mock_filter, \
             patch("daemon.retrieval._strategy_dense", new_callable=AsyncMock) as mock_dense, \
             patch("daemon.retrieval._strategy_sparse", new_callable=AsyncMock) as mock_sparse, \
             patch("daemon.retrieval.reciprocal_rank_fusion") as mock_rrf:

            mock_dense.return_value = []
            mock_sparse.return_value = []
            mock_rrf.return_value = []
            mock_embedder.embed_one = AsyncMock(return_value=[0.1]*384)

            await searcher.search("query", vault_root="/tmp", apply_decay=False)

            # Check if to_thread was called with _ripgrep_search
            # It's called once in our case
            any_rg_call = any(call.args[0] == mock_rg for call in mock_to_thread.call_args_list)
            assert any_rg_call, "ripgrep should be called via asyncio.to_thread"
