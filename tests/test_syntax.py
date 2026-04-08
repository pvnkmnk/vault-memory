"""Syntax verification tests - ensures all Python files compile."""

import py_compile
import tempfile
import os


def compile_all_modules():
    """Verify all Python modules compile without syntax errors."""
    modules = [
        "daemon/main.py",
        "daemon/config.py",
        "daemon/sync_watcher.py",
        "daemon/retrieval.py",
        "cli/mcp_adapter.py",
        "cli/main.py",
    ]

    errors = []
    for module in modules:
        try:
            py_compile.compile(module, doraise=True)
        except py_compile.PyCompileError as e:
            errors.append(f"{module}: {e}")

    if errors:
        raise AssertionError("\n".join(errors))


if __name__ == "__main__":
    import pytest

    pytest.main([__file__, "-v"])
