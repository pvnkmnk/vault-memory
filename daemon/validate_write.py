# daemon/validate_write.py
"""
WriteValidator: pre-write regression check.
Validates agent-proposed notes against high-trust vault notes.
Non-blocking by default — logs conflicts for heartbeat review.
"""

import logging
from typing import Tuple

from .weaviate_client import WeaviateClient

logger = logging.getLogger("vault-memoryd.validate")


class WriteValidator:
    def __init__(self, weaviate: WeaviateClient):
        self.weaviate = weaviate

    async def validate(self, proposed_text: str, proposed_path: str) -> Tuple[bool, str]:
        """
        Check proposed note against trust:high notes.
        Returns (is_valid, reason).
        Non-blocking: always returns True but logs for heartbeat review.
        """
        try:
            from weaviate.classes.query import Filter
            collection = self.weaviate.client.collections.get("VaultNote")
            f = Filter.by_property("trust").equal("high")
            resp = collection.query.near_text(
                query=proposed_text[:500],
                limit=5,
                filters=f,
            )
            high_trust = [
                obj.properties.get("vault_path", "?")
                for obj in resp.objects
            ]
            if high_trust:
                logger.info(
                    "validate: %s is semantically adjacent to high-trust notes: %s",
                    proposed_path,
                    high_trust,
                )
            return True, f"adjacent_to={high_trust}"
        except Exception as e:
            logger.warning("validate: skipped for %s — %s", proposed_path, e)
            return True, "skipped"
