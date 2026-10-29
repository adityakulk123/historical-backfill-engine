"""SQLite schema and storage helpers."""
import sqlite3
from pathlib import Path

def connect(db_path: str) -> sqlite3.Connection:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 30000")
    return conn

def initialize(db_path: str) -> None:
    with connect(db_path) as conn:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS customers (
            customer_id TEXT PRIMARY KEY
        );
        CREATE TABLE IF NOT EXISTS target_events (
            event_id TEXT PRIMARY KEY,
            event_date TEXT NOT NULL,
            customer_id TEXT NOT NULL REFERENCES customers(customer_id),
            event_type TEXT NOT NULL,
            amount REAL NOT NULL,
            source_file TEXT NOT NULL,
            source_row INTEGER NOT NULL,
            published_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS idx_target_events_date ON target_events(event_date);
        CREATE TABLE IF NOT EXISTS backfill_runs (
            run_id TEXT PRIMARY KEY,
            input_path TEXT NOT NULL,
            start_date TEXT NOT NULL,
            end_date TEXT NOT NULL,
            status TEXT NOT NULL,
            started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            ended_at TEXT,
            input_count INTEGER NOT NULL DEFAULT 0,
            output_count INTEGER NOT NULL DEFAULT 0,
            rejected_count INTEGER NOT NULL DEFAULT 0,
            error_message TEXT
        );
        CREATE TABLE IF NOT EXISTS backfill_partitions (
            run_id TEXT NOT NULL REFERENCES backfill_runs(run_id),
            partition_date TEXT NOT NULL,
            status TEXT NOT NULL,
            input_count INTEGER NOT NULL DEFAULT 0,
            output_count INTEGER NOT NULL DEFAULT 0,
            rejected_count INTEGER NOT NULL DEFAULT 0,
            started_at TEXT,
            ended_at TEXT,
            error_message TEXT,
            PRIMARY KEY (run_id, partition_date)
        );
        CREATE TABLE IF NOT EXISTS rejected_records (
            reject_id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL REFERENCES backfill_runs(run_id),
            partition_date TEXT NOT NULL,
            source_file TEXT NOT NULL,
            source_row INTEGER NOT NULL,
            event_id TEXT,
            raw_record TEXT NOT NULL,
            reject_reason TEXT NOT NULL,
            rejected_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(source_file, source_row, reject_reason)
        );
        """)
        conn.executemany(
            "INSERT OR IGNORE INTO customers(customer_id) VALUES (?)",
            [(f"CUST-{i:05d}",) for i in range(1, 501)]
        )

def get_counts(db_path: str) -> dict:
    with connect(db_path) as conn:
        target = conn.execute("SELECT COUNT(*) FROM target_events").fetchone()[0]
        rejected = conn.execute("SELECT COUNT(*) FROM rejected_records").fetchone()[0]
        return {"target_count": target, "rejected_count": rejected}
