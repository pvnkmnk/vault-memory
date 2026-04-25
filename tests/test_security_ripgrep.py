import shutil
import pytest
from daemon.retrieval import _ripgrep_search

@pytest.fixture
def temp_vault(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "test.md").write_text("This is a test file with a specific_pattern.")
    (vault / "another.md").write_text("Another file.")
    return vault

def test_ripgrep_search_finds_match(temp_vault):
    if not shutil.which("rg"):
        pytest.skip("ripgrep not found")

    results = _ripgrep_search("specific_pattern", str(temp_vault))
    assert results is not None
    assert len(results) > 0
    assert any("test.md" in r.vault_path for r in results)

def test_ripgrep_search_prevents_injection(temp_vault):
    if not shutil.which("rg"):
        pytest.skip("ripgrep not found")

    # Try to inject --version. If vulnerable, it might return 0 and no matches
    # but with -- it should just try to search for the string "--version" and return nothing
    results = _ripgrep_search("--version", str(temp_vault))
    assert results == []

def test_ripgrep_search_handles_dash_query(temp_vault):
    if not shutil.which("rg"):
        pytest.skip("ripgrep not found")

    (temp_vault / "dash.md").write_text("Content with --dash--")
    results = _ripgrep_search("--dash--", str(temp_vault))
    assert results is not None
    assert len(results) > 0
    assert any("dash.md" in r.vault_path for r in results)
