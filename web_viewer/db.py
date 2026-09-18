import sqlite3
from typing import Optional

from . import config

_db_conn: Optional[sqlite3.Connection] = None


def get_db_connection() -> sqlite3.Connection:
    global _db_conn
    if _db_conn is None:
        db_name = config.config.get("DB_NAME")
        if not db_name:
            raise RuntimeError("DB_NAME not set in config")
        _db_conn = sqlite3.connect(db_name, check_same_thread=False)
    return _db_conn


def dict_factory(cursor, row):
    return {col[0]: row[idx] for idx, col in enumerate(cursor.description)}