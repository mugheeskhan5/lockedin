"""SQLite connections are opened and used within the same request thread."""
import sqlite3
from contextlib import closing
from pathlib import Path
from .text_quality import repair_existing
from .delivery_migration import migrate_delivery

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "data" / "reels.db"


def connect() -> sqlite3.Connection:
    connection = sqlite3.connect(DB_PATH, timeout=5)
    connection.row_factory = sqlite3.Row
    return connection


def initialize(recover_jobs: bool = True) -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with closing(connect()) as connection:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.executescript((BASE_DIR / "schema.sql").read_text(encoding="utf-8"))
        connection.execute("BEGIN IMMEDIATE")
        migrate_delivery(connection)
        # Interrupted processing is recovered by the understanding worker only
        # while it owns the cross-process lock; startup must not steal live work.
        connection.execute("""
            INSERT OR IGNORE INTO understanding_jobs(reel_id)
            SELECT id FROM reels WHERE embedding IS NULL
        """)
        repair_existing(connection)
        connection.commit()
