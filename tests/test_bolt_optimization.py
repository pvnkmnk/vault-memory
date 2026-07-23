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
    from types import SimpleNamespace

    mock_deps = SimpleNamespace(
        settings=SimpleNamespace(lite_mode=False, vault_path="/tmp/vault"),
        postgres=MagicMock()
    )

    # Setup the mock query response
    mock_cursor = MagicMock()
    mock_cursor.fetchall.return_value = [{"target_name": "SiblingNode"}]
    mock_deps.postgres.cursor.return_value.__enter__.return_value = mock_cursor

    # We patch asyncio.to_thread to track its usage
    with patch("asyncio.to_thread", wraps=asyncio.to_thread) as mock_to_thread:
        # A search query with consecutive wildcards and long length
        long_wildcard_query = "a" * 150 + "%%_%%%%"
        req = SearchRequest(query=long_wildcard_query, top_k=5)

        # Call search_siblings
        result = asyncio.run(search_siblings(req, deps=mock_deps, _auth="ok"))

        # Verify output
        assert result == {"siblings": ["SiblingNode"], "count": 1}

        # Verify that asyncio.to_thread was called for database fetch
        assert mock_to_thread.called, "Database fetch must be called via asyncio.to_thread"

        # Verify the query argument passed to cursor.execute
        # It should be length-limited to 100 characters + wrapped in single wildcards
        called_args = mock_cursor.execute.call_args[0]
        # First element of execute is the SQL string, second element is the params tuple/list
        sql_params = called_args[1]
        passed_query = sql_params[0]

        # The raw query was 150 'a's, so the truncated sanitized query should be 100 'a's
        expected_query = f"%{'a' * 100}%"
        assert passed_query == expected_query, f"Expected {expected_query}, got {passed_query}"
