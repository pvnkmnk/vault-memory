import asyncio
from types import SimpleNamespace

from fastapi import HTTPException
import pytest

from daemon.main import SearchRequest, graph_query, search, temporal_query


def _lite_deps():
    return SimpleNamespace(
        settings=SimpleNamespace(lite_mode=True, vault_path="/tmp/vault"),
        searcher_optional=None,
    )


def test_search_returns_501_in_lite_mode():
    with pytest.raises(HTTPException) as exc:
        asyncio.run(search(SearchRequest(query="test"), deps=_lite_deps(), _auth="ok"))

    assert exc.value.status_code == 501
    assert "lite mode" in exc.value.detail.lower()


def test_graph_returns_501_in_lite_mode():
    with pytest.raises(HTTPException) as exc:
        asyncio.run(graph_query("entity", deps=_lite_deps(), _auth="ok"))

    assert exc.value.status_code == 501
    assert "lite mode" in exc.value.detail.lower()


def test_temporal_returns_501_in_lite_mode():
    with pytest.raises(HTTPException) as exc:
        asyncio.run(temporal_query("entity", deps=_lite_deps(), _auth="ok"))

    assert exc.value.status_code == 501
    assert "lite mode" in exc.value.detail.lower()
