"""Regex behavior tests for markdown parsing."""

import sys
from unittest.mock import MagicMock

import pytest


@pytest.fixture(autouse=True)
def _mock_heavy_deps():
    mock_st = MagicMock()
    mock_st.SentenceTransformer = MagicMock
    mock_st.CrossEncoder = MagicMock
    sys.modules["sentence_transformers"] = mock_st
    yield
    if "sentence_transformers" in sys.modules:
        del sys.modules["sentence_transformers"]


def test_tag_regex_basic():
    from daemon.sync_watcher import MarkdownParser
    parser = MarkdownParser()
    assert parser.TAG_RE.findall("a note with #project tag") == ["project"]
    assert parser.TAG_RE.findall("has #type/subtype inline") == ["type/subtype"]
    assert parser.TAG_RE.findall("no hash here") == []


def test_status_regex_basic():
    from daemon.sync_watcher import MarkdownParser
    parser = MarkdownParser()
    assert parser.STATUS_RE.findall("status: active") == ["active"]
    assert parser.STATUS_RE.findall("Status: working") == ["working"]
    assert parser.STATUS_RE.findall("no status here") == []
