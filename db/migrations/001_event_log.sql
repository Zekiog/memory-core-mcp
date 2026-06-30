-- P2.5-02: Neon event_log DDL
-- Target: bold-lake-40699686 (public schema)
-- Safe to re-run: IF NOT EXISTS throughout

CREATE TABLE IF NOT EXISTS public.event_log (
    id                  BIGSERIAL PRIMARY KEY,
    ts                  TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    source              TEXT         NOT NULL,
    event_type          TEXT         NOT NULL,
    severity            TEXT         NOT NULL,
    context             JSONB        NOT NULL DEFAULT '{}',
    remediation_taken   TEXT,
    outcome             TEXT
);

CREATE INDEX IF NOT EXISTS idx_event_log_ts         ON public.event_log (ts DESC);
CREATE INDEX IF NOT EXISTS idx_event_log_event_type ON public.event_log (event_type);
CREATE INDEX IF NOT EXISTS idx_event_log_severity   ON public.event_log (severity);
CREATE INDEX IF NOT EXISTS idx_event_log_source     ON public.event_log (source);

CREATE OR REPLACE VIEW public.v_loop_metrics AS
SELECT
    event_type,
    severity,
    COUNT(*)                                                           AS total,
    SUM(CASE WHEN outcome ILIKE '%ok%'   THEN 1 ELSE 0 END)          AS accepted,
    SUM(CASE WHEN outcome ILIKE '%fail%' THEN 1 ELSE 0 END)          AS failed,
    ROUND(
        SUM(CASE WHEN outcome ILIKE '%ok%' THEN 1 ELSE 0 END)::NUMERIC
        / NULLIF(COUNT(*), 0) * 100, 1
    )                                                                  AS accept_rate_pct,
    MIN(ts)                                                            AS first_seen,
    MAX(ts)                                                            AS last_seen
FROM public.event_log
WHERE ts >= NOW() - INTERVAL '7 days'
GROUP BY event_type, severity
ORDER BY total DESC;

COMMENT ON TABLE public.event_log IS
    'AVM-02 infra loop telemetry — written by n8n workflows 08/09/10 and scripts/meta_evaluator.py';
COMMENT ON VIEW  public.v_loop_metrics IS
    'Weekly accept-rate + failure distribution per event_type (meta-evaluator P2.5-05)';
