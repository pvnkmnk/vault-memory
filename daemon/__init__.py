# daemon/__init__.py
"""vault-memory daemon package."""

# Avoid eager imports of heavy optional dependencies (weaviate-client,
# psycopg2-binary, sentence-transformers) so that tests and lightweight
# tooling can import submodules without requiring all production deps.
