"""Small database adapter: Neon Postgres in production, SQLite for local development."""
from __future__ import annotations
import os
import sqlite3
from pathlib import Path

class CompatConnection:
    def __init__(self, connection, postgres):
        self.connection = connection
        self.postgres = postgres
    def execute(self, sql, params=()):
        if self.postgres:
            sql = sql.replace("?", "%s")
        return self.connection.execute(sql, params)
    def __enter__(self):
        self.connection.__enter__()
        return self
    def __exit__(self, exc_type, exc, tb):
        return self.connection.__exit__(exc_type, exc, tb)
    def close(self):
        return self.connection.close()

def connect_database():
    url = os.environ.get("DATABASE_URL", "").strip()
    if url:
        import psycopg
        return CompatConnection(psycopg.connect(url, connect_timeout=10), True)
    path = Path(os.environ.get("UGC_TRACKER_DB", "/tmp/ugc_tracker.sqlite3"))
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=30)
    connection.execute("PRAGMA journal_mode=WAL")
    return CompatConnection(connection, False)
