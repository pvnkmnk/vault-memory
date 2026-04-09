from unittest.mock import MagicMock, patch

import cli.mcp_adapter as mcp_adapter


def test_tools_list_contains_p9_tools():
    names = {tool["name"] for tool in mcp_adapter.TOOLS}
    assert "memory/promote" in names
    assert "vault_lint" in names


def test_memory_cognify_defaults_persist_true():
    captured = {}

    def fake_post(url, json=None, timeout=None, headers=None, **kwargs):
        captured["url"] = url
        captured["json"] = json
        response = MagicMock()
        response.raise_for_status.return_value = None
        response.json.return_value = {"ok": True}
        return response

    with patch("httpx.post", side_effect=fake_post):
        out = mcp_adapter._memory_cognify({"text": "hello"})

    assert out == {"ok": True}
    assert captured["url"].endswith("/cognify")
    assert captured["json"]["persist"] is True


def test_memory_promote_calls_promote_endpoint():
    captured = {}

    def fake_post(url, json=None, timeout=None, headers=None, **kwargs):
        captured["url"] = url
        captured["json"] = json
        response = MagicMock()
        response.raise_for_status.return_value = None
        response.json.return_value = {"path_written": "Knowledge/example.md"}
        return response

    args = {
        "text": "body",
        "title": "Example",
        "page_type": "analysis",
        "references": ["Alpha"],
        "vault_path": "/tmp/vault",
    }
    with patch("httpx.post", side_effect=fake_post):
        out = mcp_adapter._memory_promote(args)

    assert out["path_written"].endswith("example.md")
    assert captured["url"].endswith("/promote")
    assert captured["json"]["page_type"] == "analysis"


def test_vault_lint_calls_lint_endpoint():
    captured = {}

    def fake_post(url, json=None, timeout=None, headers=None, **kwargs):
        captured["url"] = url
        captured["json"] = json
        response = MagicMock()
        response.raise_for_status.return_value = None
        response.json.return_value = {"summary": {"total_issues": 1}}
        return response

    with patch("httpx.post", side_effect=fake_post):
        out = mcp_adapter._vault_lint({"vault_path": "/tmp/vault", "stale_days": 14})

    assert out["summary"]["total_issues"] == 1
    assert captured["url"].endswith("/lint")
    assert captured["json"]["stale_days"] == 14
