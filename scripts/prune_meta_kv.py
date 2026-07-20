#!/usr/bin/env python3
"""Meta-scope heartbeat KV prune — owner karari 2026-07-20 (aylik cron).

Siler: scope='meta' AND title='mesh-sync' AND created_at < NOW - RETENTION_DAYS.
En yeni kayit HER KOSULDA korunur. --dry-run ile sadece sayar.
"""
import argparse, json
from memory_core.config import Config
from memory_core import db

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--days', type=int, default=30)
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()
    cfg = Config.load(); db.init_pool(cfg)
    with db.conn() as c, c.cursor() as cur:
        cur.execute("SELECT id FROM memories WHERE scope='meta' AND title='mesh-sync' ORDER BY created_at DESC FETCH FIRST 1 ROWS ONLY")
        row = cur.fetchone()
        newest = row[0] if row else None
        cur.execute(
            "SELECT count(*) FROM memories WHERE scope='meta' AND title='mesh-sync' "
            "AND created_at < SYSTIMESTAMP - NUMTODSINTERVAL(:d,'DAY') AND id != :n",
            d=args.days, n=newest or '-')
        n = cur.fetchone()[0]
        if args.dry_run or n == 0:
            print(json.dumps({'would_delete': n, 'days': args.days, 'dry_run': args.dry_run}))
            return
        cur.execute(
            "DELETE FROM memory_links WHERE src_id IN (SELECT id FROM memories WHERE scope='meta' AND title='mesh-sync' "
            "AND created_at < SYSTIMESTAMP - NUMTODSINTERVAL(:d,'DAY') AND id != :n)", d=args.days, n=newest)
        cur.execute(
            "DELETE FROM memories WHERE scope='meta' AND title='mesh-sync' "
            "AND created_at < SYSTIMESTAMP - NUMTODSINTERVAL(:d,'DAY') AND id != :n", d=args.days, n=newest)
        c.commit()
        print(json.dumps({'deleted': n, 'days': args.days}))

if __name__ == '__main__':
    main()
