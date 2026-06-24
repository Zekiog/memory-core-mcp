export const STALE_THRESHOLD_MIN = 30; // 2 poll cycles (poll = 15m). Tunable.
const TS_FIELDS = ['created_at', 'createdAt', 'inserted_at', 'ts', 'timestamp'];

export function extractRecords(body) {
  if (Array.isArray(body)) return body;
  if (body && typeof body === 'object') {
    for (const k of ['records', 'data', 'results', 'items']) {
      if (Array.isArray(body[k])) return body[k];
    }
  }
  return [];
}

export function extractNewestTs(body) {
  let newest = null;
  for (const rec of extractRecords(body)) {
    for (const f of TS_FIELDS) {
      if (rec && rec[f] != null) {
        const ms = Date.parse(rec[f]);
        if (!Number.isNaN(ms) && (newest === null || ms > newest)) newest = ms;
        break;
      }
    }
  }
  return newest;
}

export function classify(httpFailed, body, nowMs, thresholdMin = STALE_THRESHOLD_MIN) {
  if (httpFailed) return 'down';
  const t = extractNewestTs(body);
  if (t === null) return 'stale';
  return (nowMs - t) / 60000 > thresholdMin ? 'stale' : 'ok';
}

export function decide(prev, current) {
  const previous = prev || 'ok';
  if (current === previous) return { alert: false, severity: null };
  if (current === 'ok') return { alert: true, severity: 'recovered' };
  return { alert: true, severity: current };
}

export function buildHeartbeatItem({ httpFailed, body, nowMs, prevState, prevSince, thresholdMin = STALE_THRESHOLD_MIN }) {
  const previous = prevState || 'ok';
  const current = classify(httpFailed, body, nowMs, thresholdMin);
  const { alert, severity } = decide(previous, current);
  const newestMs = httpFailed ? null : extractNewestTs(body);
  const ageMin = newestMs === null ? null : Math.round((nowMs - newestMs) / 60000);
  const nowIso = new Date(nowMs).toISOString();
  const nextSince = current === previous ? (prevSince || nowIso) : nowIso;

  let text = null;
  if (alert) {
    if (severity === 'down') {
      text = `🔴 *Memory Mesh DOWN* — gateway /query unreachable (avm-02:8848). Last healthy: ${prevSince || 'unknown'}.`;
    } else if (severity === 'stale') {
      text = `🟠 *Memory Mesh STALE* — newest record ${ageMin}m old (threshold ${thresholdMin}m). Gateway up; ingest may have stopped.`;
    } else {
      const dt = prevSince ? `${Math.round((nowMs - Date.parse(prevSince)) / 60000)}m` : 'unknown';
      text = `✅ *Memory Mesh RECOVERED* — flow normal. Newest record ${ageMin ?? '?'}m old. Downtime ≈ ${dt}.`;
    }
  }

  return {
    alert,
    severity,
    current,
    ageMin,
    newestTs: newestMs === null ? null : new Date(newestMs).toISOString(),
    text,
    nextState: current,
    nextSince,
  };
}
