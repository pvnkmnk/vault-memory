# daemon/__init__.py
"""vault-memory daemon package.

This package intentionally keeps its __init__.py minimal. Heavy optional
dependencies (weaviate, sentence-transformers, psycopg2) are imported only by
the submodules that need them, so lite-mode and test imports of lightweight
submodules do not fail when those dependencies are absent.
"""

# No eager submodule imports here. Import the specific submodule you need, e.g.:
#   from daemon.config import Settings
#   from daemon.dependencies import Dependencies
