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
