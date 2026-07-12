-- ============================================================================
-- Memory-core Neon migration 001
-- public.event_log  (events.py native table)
-- memory_bus.events (projections.sql contract view)
-- ----------------------------------------------------------------------------
-- Idempotent. Run at every boot or manually:
--   psql $NEON_EVENT_DSN -f migrations/001_event_log.sql
-- ============================================================================

-- Required extensions
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- Schema for bus projections
CREATE SCHEMA IF NOT EXISTS memory_bus;

-- ---------------------------------------------------------------------------
-- 1. public.event_log — native table (events.py INSERT target)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.event_log (
    id                BIGSERIAL    PRIMARY KEY,
    ts                TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    source            TEXT         NOT NULL,
    event_type        TEXT         NOT NULL,
    severity          TEXT         NOT NULL DEFAULT 'info',
    context           JSONB        NOT NULL DEFAULT '{}'::jsonb,
    remediation_taken TEXT,
    outcome           TEXT
);

CREATE INDEX IF NOT EXISTS event_log_ts_idx
    ON public.event_log (ts DESC);
CREATE INDEX IF NOT EXISTS event_log_event_type_idx
    ON public.event_log (event_type, ts DESC);
CREATE INDEX IF NOT EXISTS event_log_source_idx
    ON public.event_log (source, ts DESC);
CREATE INDEX IF NOT EXISTS event_log_context_agent_idx
    ON public.event_log ((context ->> 'agent_id'), ts DESC);
CREATE INDEX IF NOT EXISTS event_log_context_op_idx
    ON public.event_log ((context ->> 'operation_id'), ts DESC);

COMMENT ON TABLE public.event_log IS
  'Event-sourced memory bus: infra + MEM_* agent events. Written by events.py log_event().';

-- ---------------------------------------------------------------------------
-- 2. memory_bus.v_agent_state — projection (adapter-hooks.md §5 fallback path)
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW memory_bus.v_agent_state AS
WITH last_per_surface AS (
    SELECT  context ->> 'agent_id'   AS agent_id,
            context ->> 'workspace'  AS workspace,
            context ->> 'surface'    AS surface,
            MAX(ts)                  AS last_event_at,
            COUNT(*)                 AS event_count_30d
    FROM    public.event_log
    WHERE   ts >= NOW() - INTERVAL '30 days'
      AND   event_type IN ('mem_query','mem_ingest','mem_no_fallback',
                           'mem_fallback_used','MEM_QUERY','MEM_INGEST',
                           'MEM_NO_FALLBACK','MEM_FALLBACK_USED')
    GROUP BY context ->> 'agent_id', context ->> 'workspace', context ->> 'surface'
),
last_ingest AS (
    SELECT DISTINCT ON (
        context ->> 'agent_id',
        context ->> 'workspace',
        context ->> 'surface'
    )
           context ->> 'agent_id'              AS agent_id,
           context ->> 'workspace'             AS workspace,
           context ->> 'surface'               AS surface,
           context ->> 'operation_id'          AS operation_id,
           context                             AS context,
           ts                                  AS occurred_at
    FROM   public.event_log
    WHERE  event_type IN ('mem_ingest','MEM_INGEST')
    ORDER BY
        context ->> 'agent_id',
        context ->> 'workspace',
        context ->> 'surface',
        ts DESC
)
SELECT  lps.agent_id,
        lps.workspace,
        lps.surface,
        lps.last_event_at,
        lps.event_count_30d,
        li.operation_id     AS last_ingest_op,
        li.context          AS last_ingest_context,
        li.occurred_at      AS last_ingest_at
FROM    last_per_surface lps
LEFT JOIN last_ingest li
       ON  li.agent_id  = lps.agent_id
       AND li.workspace = lps.workspace
       AND li.surface   = lps.surface;

COMMENT ON VIEW memory_bus.v_agent_state IS
  'Latest memory snapshot per (agent_id, workspace, surface). '
  'Used by MemoryAdapter.recall fallback path (Phase 4.1).';

-- ---------------------------------------------------------------------------
-- 3. memory_bus.v_loop_metrics — per-operation rollup
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW memory_bus.v_loop_metrics AS
SELECT  context ->> 'operation_id'  AS operation_id,
        context ->> 'agent_id'      AS agent_id,
        context ->> 'workspace'     AS workspace,
        context ->> 'surface'       AS surface,
        MIN(ts)                     AS started_at,
        MAX(ts)                     AS ended_at,
        EXTRACT(EPOCH FROM (MAX(ts) - MIN(ts))) AS wallclock_s,
        bool_or(event_type IN ('mem_query','MEM_QUERY'))           AS had_query,
        bool_or(event_type IN ('mem_ingest','MEM_INGEST'))         AS had_ingest,
        bool_or(event_type IN ('mem_no_fallback','MEM_NO_FALLBACK')) AS had_no_fallback,
        bool_or(event_type IN ('mem_fallback_used','MEM_FALLBACK_USED')) AS had_fallback,
        bool_or(event_type IN ('mem_consistency_warn','MEM_CONSISTENCY_WARN')) AS had_warn,
        COUNT(*) FILTER (
            WHERE event_type IN ('mem_replay_start','MEM_REPLAY_START')
        ) AS replay_starts,
        COUNT(*) FILTER (
            WHERE event_type IN ('mem_replay_done','MEM_REPLAY_DONE')
        ) AS replay_dones
FROM    public.event_log
WHERE   context ? 'operation_id'
GROUP BY
        context ->> 'operation_id',
        context ->> 'agent_id',
        context ->> 'workspace',
        context ->> 'surface';

COMMENT ON VIEW memory_bus.v_loop_metrics IS
  'Per-operation rollup of MEM_* events. Drives meta_evaluator thresholds.';
