#!/usr/bin/env python3
"""meta_evaluator.py — P2.5-05 weekly loop quality analysis.

Runs every Sunday at 23:00 (cron or n8n Execute-Command node).
Reads Neon v_loop_metrics, computes accept-rate and false-positive rate,
logs META_EVAL_RUN, and sends a Telegram summary with any flags.

Env vars:
  NEON_EVENT_DSN         postgresql://...
  TELEGRAM_BOT_TOKEN     optional
  TELEGRAM_CHAT_ID       optional
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone

import psycopg2
import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from memory_core.events import EventType, Severity, log_event

NEON_DSN     = os.environ["NEON_EVENT_DSN"]
TG_TOKEN     = os.environ.get("TELEGRAM_BOT_TOKEN")
TG_CHAT      = os.environ.get("TELEGRAM_CHAT_ID")
ACCEPT_FLOOR = 0.50
FP_CEIL      = 0.20


def fetch_metrics() -> list[dict]:
    conn = psycopg2.connect(NEON_DSN)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM public.v_loop_metrics")
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]
    finally:
        conn.close()


def send_telegram(text: str) -> None:
    if not (TG_TOKEN and TG_CHAT):
        print("[meta_evaluator] Telegram not configured — printing only")
        print(text)
        return
    requests.post(
        f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
        json={"chat_id": TG_CHAT, "text": text, "parse_mode": "Markdown"},
        timeout=10,
    )


def main() -> None:
    now = datetime.now(timezone.utc)
    metrics = fetch_metrics()
    flags: list[dict] = []
    lines = [f"*Loop Meta-Evaluator Report*\n_{now.strftime('%Y-%m-%d %H:%M UTC')}_\n"]

    for m in metrics:
        accept_pct = float(m.get("accept_rate_pct") or 0)
        total      = int(m.get("total") or 0)
        failed     = int(m.get("failed") or 0)
        fp_rate    = round(failed / total, 4) if total else 0.0
        et         = m["event_type"]

        status = "ok"
        if accept_pct / 100 < ACCEPT_FLOOR and total >= 5:
            status = "LOW_ACCEPT"
            flags.append({"event_type": et, "issue": "low_accept_rate",
                          "accept_rate_pct": accept_pct,
                          "suggestion": f"Review trigger threshold for {et}"})
        elif fp_rate > FP_CEIL and total >= 5:
            status = "HIGH_FP"
            flags.append({"event_type": et, "issue": "high_false_positive",
                          "fp_rate": fp_rate,
                          "suggestion": f"Raise trigger threshold for {et}"})

        lines.append(
            f"`{status}` `{et}` — total={total}, "
            f"accept={accept_pct}%, fp={fp_rate*100:.1f}%"
        )

    summary = "\n".join(lines)
    if flags:
        summary += "\n\n*Flags requiring review:*\n"
        for f in flags:
            summary += f"  {f['event_type']}: {f['issue']} -> {f['suggestion']}\n"

    log_event(
        source="meta_evaluator.py",
        event_type=EventType.META_EVAL_RUN,
        severity=Severity.INFO,
        context={"metrics_count": len(metrics), "flags_count": len(flags)},
        outcome="ok" if not flags else f"{len(flags)} flags raised",
    )
    for f in flags:
        log_event(
            source="meta_evaluator.py",
            event_type=EventType.META_EVAL_THRESHOLD,
            severity=Severity.WARN,
            context=f,
            outcome="awaiting_human_approval",
        )

    send_telegram(summary)
    print("[meta_evaluator] Done.", len(flags), "flag(s) raised.")


if __name__ == "__main__":
    main()
