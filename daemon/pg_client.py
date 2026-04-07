# daemon/pg_client.py
import logging
import psycopg2
from psycopg2.extras import RealDictCursor

logger = logging.getLogger("vault-memoryd.pg")


class PostgresClient:
    def __init__(self, connection_string: str):
        self.conn = psycopg2.connect(
            connection_string,
            cursor_factory=RealDictCursor,
        )
        self.conn.autocommit = False
        logger.info("PostgreSQL connected")

    async def ping(self):
        cursor = self.conn.cursor()
        cursor.execute("SELECT 1")
        cursor.close()

    def close(self):
        self.conn.close()
