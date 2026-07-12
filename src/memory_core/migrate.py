"""migrate.py — Idempotent Neon DDL migrations for memory-core.

Runs all *.sql files in the ``migrations/`` directory (adjacent to this
package's project root) in lexicographic order. Each file is executed as
a single transaction; if it fails the error is logged and the server
continues — DDL is idempotent (IF NOT EXISTS everywhere).

Trigger: called once at server startup from _build_http_app().
Skipped silently when NEON_EVENT_DSN is not set.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

log = logging.getLogger(__name__)

_MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "migrations"


def run_migrations(dsn: str | None = None) -> None:
    """Run all pending Neon DDL migrations. Never raises; logs errors."""
    effective_dsn = dsn or os.environ.get("NEON_EVENT_DSN", "")
    if not effective_dsn:
        log.debug("migrate: NEON_EVENT_DSN not set, skipping Neon migrations")
        return
    if not _MIGRATIONS_DIR.is_dir():
        log.debug("migrate: migrations dir not found: %s", _MIGRATIONS_DIR)
        return

    sql_files = sorted(_MIGRATIONS_DIR.glob("*.sql"))
    if not sql_files:
        log.debug("migrate: no .sql files in %s", _MIGRATIONS_DIR)
        return

    try:
        import psycopg2  # type: ignore
    except ImportError:
        log.warning("migrate: psycopg2 not installed, skipping migrations")
        return

    for sql_file in sql_files:
        _run_file(effective_dsn, sql_file)


def _run_file(dsn: str, sql_file: Path) -> None:
    try:
        import psycopg2  # type: ignore
        conn = psycopg2.connect(dsn)
        try:
            with conn.cursor() as cur:
                sql = sql_file.read_text(encoding="utf-8")
                cur.execute(sql)
            conn.commit()
            log.info("migrate: applied %s", sql_file.name)
        except Exception as exc:
            conn.rollback()
            log.error("migrate: FAILED %s: %s", sql_file.name, exc)
        finally:
            conn.close()
    except Exception as exc:
        log.error("migrate: connection failed for %s: %s", sql_file.name, exc)
