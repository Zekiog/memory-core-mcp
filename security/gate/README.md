# Mesh Gate — avm-02 continuous public-exposure assurance

Read-only security sensor (`gate_check.sh`, 4 ADIM) + scheduling + alerting + audit.
Design: `../../docs/superpowers/specs/2026-06-29-mesh-gate-continuous-design.md`.

## What it does
- ADIM 1 `ss` internal bind · 2 `ufw`+DOCKER-USER · 3 `docker` host-bind · 4 external probe.
- Verdict GREEN(0)/RED(1)/CONFIG(2). `FORMAT=text|json|prom`.
- avm-01 runs it every 15m: prom -> node_exporter textfile; json -> append-only ledger.
- Alerts: MeshGateRED (verdict=0), MeshGateStale (sensor dead >1h), MeshGateConfigError (gate blind >15m).

## Local test
    bats security/gate/tests/

## Deploy (operator-gated — touches avm-01 only; NEVER mutates avm-02)
1. Copy `security/gate/` to avm-01 `/opt/mesh-gate/` (keep `lib/` next to `gate_check.sh`).
2. Confirm node_exporter runs with `--collector.textfile.directory=<DIR>`; set `TEXTFILE_DIR` in the unit to `<DIR>`.
3. Smoke (read-only): `FORMAT=text /opt/mesh-gate/gate_check.sh` → expect a `gate=GREEN ...` line, exit 0.
4. Install units: copy `systemd/mesh-gate.{service,timer}` to `/etc/systemd/system/`, `systemctl daemon-reload`.
5. `systemd-analyze verify mesh-gate.service mesh-gate.timer`.
6. Drop `prometheus/mesh_gate.rules.yml` into the Prometheus rules dir; `promtool check rules` it; reload Prometheus.
7. Add a Slack `#security` receiver to Alertmanager (webhook provided out-of-band, never committed); reload Alertmanager.
8. `systemctl enable --now mesh-gate.timer`. Confirm first run: `systemctl start mesh-gate.service && cat <DIR>/mesh_gate.prom`.

## Rollback (clean, additive — no avm-02 state ever touched)
    systemctl disable --now mesh-gate.timer
    rm -f <DIR>/mesh_gate.prom <DIR>/mesh_gate.prom.tmp
    rm -f /etc/systemd/system/mesh-gate.service /etc/systemd/system/mesh-gate.timer
    systemctl daemon-reload
    # remove mesh_gate.rules.yml from the Prometheus rules dir; reload Prometheus

## Follow-on (not in this cycle)
- Hash-chain the ledger (prev_sha256/sha256); ship verdicts to ADB via `zmem add` (scope security-gate).
- Wire `bats security/gate/tests/` into repo CI.
- Max-external: independent internet-side prober (true off-ASN vantage).
- P2.1 backstop application (DOCKER-USER DROP rules) — separate operator-gated change.
