"""Connection pool + minimal forward-only migration runner.
Direct Postgres (psycopg3) instead of a REST data API: the design needs
transactions, row locks (SKIP LOCKED) and compare-and-swap updates."""
import os
from pathlib import Path
from typing import Optional

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from app.config import settings
from app.logging_setup import get_logger

log = get_logger(component="db")

_pool: Optional[ConnectionPool] = None

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"


def get_pool() -> ConnectionPool:
    global _pool
    if _pool is None:
        _pool = ConnectionPool(
            settings.DATABASE_URL,
            min_size=1,
            max_size=int(os.getenv("DB_POOL_MAX", "10")),
            kwargs={"autocommit": False, "row_factory": dict_row},
            open=True,
        )
    return _pool


def close_pool() -> None:
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None


def run_migrations() -> None:
    """Apply migrations/*.sql in filename order, exactly once each."""
    with get_pool().connection() as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            "filename text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())"
        )
        applied = {
            r["filename"]
            for r in conn.execute("SELECT filename FROM schema_migrations").fetchall()
        }
        for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
            if path.name in applied:
                continue
            log.info("applying_migration", filename=path.name)
            conn.execute(path.read_text(encoding="utf-8"))
            conn.execute(
                "INSERT INTO schema_migrations (filename) VALUES (%s) ON CONFLICT DO NOTHING",
                (path.name,),
            )
        conn.commit()


def ping() -> bool:
    try:
        with get_pool().connection() as conn:
            conn.execute("SELECT 1")
        return True
    except psycopg.Error:
        return False
