# AVM-02 Infra Loop Plan (P2.x)

Bu dosya, Neon `public.infra_plans` tablosuna kaydedilmiş 20 görevlik AVM-02 altyapı planını, memory-core-mcp perspektifinden uygulanabilir loop ve n8n workflow modeli olarak özetler.

## Track'ler

- P2.3-security
- P2.4-spof-ha
- P2.5-telemetry-healing
- P2N-integrations

Her görev için `plan_id`, n8n workflow adı ve ilgili script/policy dosyası eşlemesi vardır.

## P2.3 — Security Posture

- P2.3-01 → docs/security/entrypoint-inventory.md (manuel + n8n catalog sync)
- P2.3-02 → security/github-runner-groups.md + actions-runner config
- P2.3-03 → n8n webhook auth + HMAC middleware
- P2.3-04 → deploy/cloudflare-zero-trust.md + CF Access config
- P2.3-05 → scripts/secret-rotation.sh + Neon event_log yazma

## P2.4 — SPOF / HA

- P2.4-01 → docs/infra/runner-spof-analysis.md
- P2.4-02 → scripts/n8n-backup.sh + docs/infra/n8n-restore-runbook.md
- P2.4-03 → deploy/avm-03-provisioning.md
- P2.4-04 → docs/infra/runner-routing-strategy.md
- P2.4-05 → docs/infra/n8n-queue-ha.md + Redis config

## P2.5 — Telemetry & Self-Healing

- P2.5-01 → Prometheus metrics (node, runner, n8n, CF)
- P2.5-02 → Neon event_log schema + src/memory_core/events.py
- P2.5-03 → self-healing tier-1 (low-risk otomasyon)
- P2.5-04 → self-healing tier-2 (medium-risk, onaylı)
- P2.5-05 → meta-evaluator (haftalık loop analizi)

## P2N — n8n / Zapier / CF Tunnel

- P2N-01 → docs/integrations/flow-risk-matrix.md
- P2N-02 → integrations/zapier-webhook-hardening.md
- P2N-03 → deploy/cloudflare-tunnel-no-open-ports.md
- P2N-04 → docs/monitoring/unified-alerts.md
- P2N-05 → docs/integrations/resilience-dead-letter.md
