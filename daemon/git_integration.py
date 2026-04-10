# daemon/git_integration.py
"""
Git integration for vault-memory.
Provides GitContext for branch-aware sessions and incremental sync.
"""

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Set
import logging

logger = logging.getLogger("vault-memory.git")

# Optional: gitpython for advanced git operations
try:
    from git import Repo, GitCommandError

    HAS_GITPYTHON = True
except ImportError:
    HAS_GITPYTHON = False


@dataclass
class GitContext:
    """Git context for a vault."""

    vault_path: Path
    branch: str = "main"
    commit: str = ""
    is_repo: bool = False
    last_commit_hash: str = ""
    changed_files: List[str] = None

    def __post_init__(self):
        if self.changed_files is None:
            self.changed_files = []


def get_git_context(vault_path: Path) -> GitContext:
    """Get git context for a vault path."""
    ctx = GitContext(vault_path=vault_path)

    if not HAS_GITPYTHON:
        # Fallback to CLI
        return _get_git_context_cli(vault_path) or ctx

    try:
        repo = Repo(vault_path, search_parent_directories=True)
        ctx.is_repo = True
        ctx.branch = repo.active_branch.name
        ctx.commit = repo.head.commit.hexsha[:8]
        ctx.last_commit_hash = repo.head.commit.hexsha

        # Get changed files since last sync
        try:
            # Get diff between working tree and last commit
            diff = repo.head.commit.diff(None)
            ctx.changed_files = [item.a_path for item in diff]
        except Exception:
            ctx.changed_files = []

        return ctx
    except (GitCommandError, Exception) as e:
        logger.debug("Not a git repo or git error: %s", e)
        return ctx


def _get_git_context_cli(vault_path: Path) -> Optional[GitContext]:
    """Fallback CLI-based git context detection."""
    ctx = GitContext(vault_path=vault_path)

    try:
        # Check if .git exists
        git_dir = vault_path / ".git"
        if not git_dir.exists():
            return None

        # Get branch
        result = subprocess.run(
            ["git", "branch", "--show-current"],
            cwd=vault_path,
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            ctx.branch = result.stdout.strip() or "main"

        # Get commit
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=vault_path,
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            ctx.commit = result.stdout.strip()[:8]
            ctx.last_commit_hash = result.stdout.strip()

        ctx.is_repo = True
        return ctx

    except Exception as e:
        logger.debug("Git CLI error: %s", e)
        return None


def get_changed_files_since(
    vault_path: Path,
    since_commit: str,
) -> List[str]:
    """Get list of files changed since a specific commit."""
    if not since_commit:
        return []

    if HAS_GITPYTHON:
        try:
            repo = Repo(vault_path, search_parent_directories=True)
            commit = repo.commit(since_commit)
            # Get files in commit
            return [item.a_path for item in commit.diff(commit.parents[0])]
        except Exception:
            return []

    # CLI fallback
    try:
        result = subprocess.run(
            ["git", "diff", "--name-only", f"{since_commit}..HEAD"],
            cwd=vault_path,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            return [f.strip() for f in result.stdout.strip().split("\n") if f.strip()]
    except Exception:
        pass

    return []


def install_git_hooks(vault_path: Path) -> Dict[str, Any]:
    """Install git hooks for automatic sync."""
    hooks_dir = vault_path / ".git" / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)

    installed: List[str] = []
    errors: List[str] = []

    hook_scripts = {
        "post-commit": """#!/bin/bash
# Auto-sync after commit
vault-memory sync --vault "$PWD" 2>/dev/null || true
""",
        "post-merge": """#!/bin/bash
# Auto-sync after merge
vault-memory sync --vault "$PWD" 2>/dev/null || true
""",
        "post-checkout": """#!/bin/bash
# Auto-sync after checkout
vault-memory sync --vault "$PWD" 2>/dev/null || true
""",
    }

    for hook_name, script in hook_scripts.items():
        hook_path = hooks_dir / hook_name
        try:
            hook_path.write_text(script, encoding="utf-8")
            hook_path.chmod(0o755)  # Make executable
            installed.append(hook_name)
        except Exception as e:
            errors.append(f"{hook_name}: {e}")

    return {
        "installed": installed,
        "errors": errors,
        "vault_path": str(vault_path),
    }


def remove_git_hooks(vault_path: Path) -> Dict[str, Any]:
    """Remove installed git hooks."""
    hooks_dir = vault_path / ".git" / "hooks"
    removed: List[str] = []
    errors: List[str] = []

    hook_names = ["post-commit", "post-merge", "post-checkout"]

    for hook_name in hook_names:
        hook_path = hooks_dir / hook_name
        if hook_path.exists():
            try:
                hook_path.unlink()
                removed.append(hook_name)
            except Exception as e:
                errors.append(f"{hook_name}: {e}")

    return {
        "removed": removed,
        "errors": errors,
        "vault_path": str(vault_path),
    }
