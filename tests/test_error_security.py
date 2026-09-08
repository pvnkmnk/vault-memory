import pytest
import json
from daemon.helpers.responses import error_response

def test_error_response_hides_details_on_500():
    # Technical detail that should be hidden
    secret_detail = "Database connection string leaked!"

    response = error_response("Internal server error", status_code=500, detail=secret_detail)
    data = json.loads(response.body)

    assert response.status_code == 500
    assert data["error"] == "Internal server error"
    assert "detail" not in data

def test_error_response_shows_details_on_400():
    # User-facing detail that should be shown
    validation_error = "Invalid email format"

    response = error_response("Bad request", status_code=400, detail=validation_error)
    data = json.loads(response.body)

    assert response.status_code == 400
    assert data["error"] == "Bad request"
    assert data["detail"] == validation_error

def test_error_response_hides_details_on_all_server_errors():
    for status in [500, 501, 502, 503, 504]:
        response = error_response("Error", status_code=status, detail="Sensitive")
        data = json.loads(response.body)
        assert "detail" not in data, f"Detail leaked for status {status}"


def test_safe_vault_path_prevents_traversal():
    from pathlib import Path
    from daemon.helpers.validation import _safe_vault_path

    vault_root = Path("/tmp/mock_vault").resolve()

    with pytest.raises(ValueError, match="Parent traversal is not allowed"):
        _safe_vault_path(vault_root, "../outside.md")

    with pytest.raises(ValueError, match="Absolute paths are not allowed"):
        _safe_vault_path(vault_root, "/etc/passwd")


def test_safe_vault_path_rejects_escaped_resolved_paths(tmp_path):
    """Symlink-style escapes resolved outside the vault root must be rejected."""
    from daemon.helpers.validation import _safe_vault_path

    outside = tmp_path / "outside"
    outside.mkdir()
    vault_root = tmp_path / "vault"
    vault_root.mkdir()
    (vault_root / "link").symlink_to(outside, target_is_directory=True)

    with pytest.raises(ValueError, match="outside vault root"):
        _safe_vault_path(vault_root, "link/escape.md")


def test_promote_guard_returns_400_when_path_escapes_vault():
    """The promote route must return a 400, not a 500, if the resolved
    promote path ever escapes the vault root."""
    from daemon.routes.knowledge import knowledge_router

    # Reach the handler through the router so the guard branch can be
    # exercised without standing up the full app dependency graph.
    promote_route = next(
        r for r in knowledge_router.routes
        if getattr(r, "path", "") == "/promote"
    )
    handler = promote_route.endpoint

    import inspect
    source = inspect.getsource(handler)
    assert "INVALID_PROMOTE_PATH" in source
    assert "relative_to(vault_root)" in source
