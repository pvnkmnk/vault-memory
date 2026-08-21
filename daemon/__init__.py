# daemon/__init__.py
"""vault-memory daemon package.

Imports are kept lazy to avoid loading heavy optional dependencies
(e.g. sentence_transformers, psycopg2, weaviate-client) when only a
small submodule such as daemon.models or daemon.routes is needed.
"""
