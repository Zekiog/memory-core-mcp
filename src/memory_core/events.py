"""events.py — Neon event_log writer for AVM-02 self-healing loops.

Separate from db.py (Oracle ADB) intentionally:
  db.py    → Oracle ADB 23ai  (vector memory store)
  events.py → Neon PostgreSQL (infra event log, loop state, meta-eval)

Env vars required:
  NEON_EVENT_DSN   postgresql://user:pass@host/dbname?sslmode=require

Usage:
  from memory_core.events import log_event, EventType, Severity
  log_event(
      source="08-self-healing-tier1",
      event_type=EventType.DISK_WARN,
      severity=Severity.WARN,
      context={"disk_pct": 87, "host": "avm02"},
      remediation_taken="runner-cleanup.sh",
      outcome="disk_pct reduced to 74",
  )
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any

try:
    import psycopg2
    import psycopg2.pool as pg_pool
except ImportError as exc:
    raise ImportError(
        "psycopg2-binary required: pip install psycopg2-binary"
    ) from exc


# ---------------------------------------------------------------------------
# Event vocabulary
# ---------------------------------------------------------------------------

class EventType(StrEnum):
    DISK_WARN          = "disk_warn"
    DISK_CRIT          = "disk_crit"
    DISK_CLEANUP_OK    = "disk_cleanup_ok"
    DISK_CLEANUP_FAIL  = "disk_cleanup_fail"
    JOB_START          = "job_start"
    JOB_FAIL           = "job_fail"
    JOB_FAIL_STREAK    = "job_fail_streak"
    RUNNER_RESTART     = "runner_restart"
    RUNNER_DOWN_ALL    = "runner_down_all"
    RUNNER_SPAWN       = "runner_spawn"
    N8N_FLOW_ERROR     = "n8n_flow_error"
    N8N_ALL_FAIL       = "n8n_all_fail"
    N8N_RESTORE_DRYRUN = "n8n_restore_dryrun"
    N8N_SUSPEND        = "n8n_suspend"
    N8N_RESUME         = "n8n_resume"
    WEBHOOK_BLOCKED    = "webhook_blocked"
    WEBHOOK_SPAM       = "webhook_spam"
    SECRET_ROTATED     = "secret_rotated"
    CF_TUNNEL_DOWN     = "cf_tunnel_down"
    CF_TUNNEL_UP       = "cf_tunnel_up"
    CF_POLICY_DRIFT    = "cf_policy_drift"
    HA_HEALTH_FAIL     = "ha_health_fail"
    HA_HEALTH_OK       = "ha_health_ok"
    ROLLBACK_TRIGGERED = "rollback_triggered"
    META_EVAL_RUN      = "meta_eval_run"
    META_EVAL_THRESHOLD = "meta_eval_threshold_change"
    LOOP_START         = "loop_start"
    LOOP_STOP          = "loop_stop"
    HUMAN_OVERRIDE     = "human_override"


class Severity(StrEnum):
    INFO     = "info"
    WARN     = "warn"
    ERROR    = "error"
    CRITICAL = "critical"


# ---------------------------------------------------------------------------
# Connection pool (lazy init, module-level singleton)
# ---------------------------------------------------------------------------

_pool: pg_pool.SimpleConnectionPool | None = None


def _get_pool() -> pg_pool.SimpleConnectionPool:
    global _pool
    if _pool is not None:
        return _pool
    dsn = os.environ.get("NEON_EVENT_DSN")
    if not dsn:
        raise RuntimeError("NEON_EVENT_DSN env var not set")
    _pool = pg_pool.SimpleConnectionPool(minconn=1, maxconn=4, dsn=dsn)
    return _pool


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def log_event(
    *,
    source: str,
    event_type: str | EventType,
    severity: str | Severity,
    context: dict[str, Any] | None = None,
    remediation_taken: str | None = None,
    outcome: str | None = None,
) -> int:
    """Insert one row into public.event_log on Neon. Returns the row id."""
    pool = _get_pool()
    conn = pool.getconn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO event_log
                    (ts, source, event_type, severity, context,
                     remediation_taken, outcome)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (
                    datetime.now(timezone.utc),
                    str(source),
                    str(event_type),
                    str(severity),
                    json.dumps(context or {}),
                    remediation_taken,
                    outcome,
                ),
            )
            row_id: int = cur.fetchone()[0]
        conn.commit()
        return row_id
    except Exception:
        conn.rollback()
        raise
    finally:
        pool.putconn(conn)


def query_events(
    *,
    event_types: list[str] | None = None,
    severity: str | None = None,
    source: str | None = None,
    since_hours: int = 24,
    limit: int = 200,
) -> list[dict]:
    """Read recent events from event_log."""
    filters = ["ts >= NOW() - INTERVAL '%s hours'"]
    binds: list[Any] = [since_hours]
    if event_types:
        filters.append("event_type = ANY(%s)")
        binds.append(event_types)
    if severity:
        filters.append("severity = %s")
        binds.append(severity)
    if source:
        filters.append("source = %s")
        binds.append(source)
    where = " AND ".join(filters)
    sql = f"SELECT * FROM event_log WHERE {where} ORDER BY ts DESC LIMIT %s"
    binds.append(limit)
    pool = _get_pool()
    conn = pool.getconn()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, binds)
            cols = [d[0] for d in cur.description]
            return [
                {k: (v.isoformat() if hasattr(v, "isoformat") else v)
                 for k, v in zip(cols, row)}
                for row in cur.fetchall()
            ]
    finally:
        pool.putconn(conn)


def accept_rate(
    *,
    event_type: str,
    outcome_ok_keyword: str = "ok",
    since_hours: int = 168,
) -> float:
    """accept-rate = rows where outcome ILIKE '%ok%' / total. 0.0 if no data."""
    pool = _get_pool()
    conn = pool.getconn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    COUNT(*) AS total,
                    SUM(CASE WHEN outcome ILIKE %s THEN 1 ELSE 0 END) AS accepted
                FROM event_log
                WHERE event_type = %s
                  AND ts >= NOW() - INTERVAL '1 hour' * %s
                """,
                (f"%{outcome_ok_keyword}%", event_type, since_hours),
            )
            total, accepted = cur.fetchone()
            if not total:
                return 0.0
            return round((accepted or 0) / total, 4)
    finally:
        pool.putconn(conn)
