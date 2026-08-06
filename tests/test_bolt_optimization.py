import asyncio
from unittest.mock import MagicMock, AsyncMock, patch
from daemon.retrieval import UnifiedSearch

def test_search_parallelization():
    # Mock dependencies
    mock_weaviate = MagicMock()
    mock_postgres = MagicMock()
    mock_embedder = MagicMock()

    # Track calls to embedder and whether sparse started before embedding completed
    embed_call_event = asyncio.Event()
    sparse_started_before_embed = asyncio.Event()

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

        async def sparse_side_effect(*args, **kwargs):
            if not embed_call_event.is_set():
                sparse_started_before_embed.set()
            await asyncio.sleep(0.01)
            return []

        mock_sparse.side_effect = sparse_side_effect
        mock_rrf.return_value = []

        searcher = UnifiedSearch(mock_weaviate, mock_postgres, mock_embedder)

        # Run search
        asyncio.run(searcher.search("test query", apply_decay=False))

        # Verify dense strategy was called (it calls embed_one internally now)
        mock_dense.assert_called_once()
        # Verify sparse strategy was called in parallel (it doesn't wait for embed_one)
        mock_sparse.assert_called_once()
        assert sparse_started_before_embed.is_set(), (
            "sparse strategy should start before embedding completes"
        )

def test_ripgrep_to_thread():
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

            asyncio.run(searcher.search("query", vault_root="/tmp", apply_decay=False))

            # Check if to_thread was called with _ripgrep_search
            # It's called once in our case
            any_rg_call = any(call.args[0] == mock_rg for call in mock_to_thread.call_args_list)
            assert any_rg_call, "ripgrep should be called via asyncio.to_thread"


def test_search_siblings_optimization():
    from daemon.routes.search import search_siblings
    from daemon.models.search import SearchRequest

    mock_deps = MagicMock()
    mock_deps.settings.lite_mode = False
    mock_cursor = mock_deps.postgres.cursor.return_value.__enter__.return_value
    mock_cursor.fetchall.return_value = [{"target_name": "sibling1"}]

    req = SearchRequest(query="test_query_%", top_k=5)

    with patch("daemon.routes.search.sanitize_like_query", wraps=lambda x: x.replace("%", "\\%")) as mock_sanitize, \
         patch("asyncio.to_thread", wraps=asyncio.to_thread) as mock_to_thread:

        response = asyncio.run(search_siblings(req, mock_deps))

        mock_sanitize.assert_called_once_with("test_query_%")
        mock_to_thread.assert_called_once()
        assert response == {"siblings": ["sibling1"], "count": 1}


def test_temporal_query_optimization():
    from daemon.routes.temporal import temporal_query

    mock_deps = MagicMock()
    mock_deps.settings.lite_mode = False
    mock_cursor = mock_deps.postgres.cursor.return_value.__enter__.return_value
    import datetime
    mock_cursor.fetchall.return_value = [{
        "entity_name": "entity1",
        "date": datetime.date(2025, 5, 20),
        "centrality": 0.5,
        "node_type": "concept",
        "vault_path": "path/to/note",
        "last_seen": datetime.datetime(2025, 5, 20, 12, 0, 0)
    }]

    with patch("daemon.routes.temporal.sanitize_like_query", wraps=lambda x: x.replace("%", "\\%")) as mock_sanitize, \
         patch("asyncio.to_thread", wraps=asyncio.to_thread) as mock_to_thread:

        response = asyncio.run(temporal_query(entity="test%", limit=5, deps=mock_deps))

        mock_sanitize.assert_called_once_with("test%")
        mock_to_thread.assert_called_once()
        assert len(response["entities"]) == 1
        assert response["entities"][0]["entity_name"] == "entity1"
        assert response["entities"][0]["date"] == "2025-05-20"
        assert response["entities"][0]["last_seen"] == "2025-05-20T12:00:00"
