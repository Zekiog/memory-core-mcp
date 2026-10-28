# Semantic Layer — memory-core-mcp mirror

> Mirror of the canonical spec at
> <https://github.com/Zekiog/zion-os/blob/main/docs/semantic-layer.md>.
> This file lives in the implementation repo so engineers working on the
> semantic layer have the spec one click away. **Edit the canonical version
> first, then re-mirror.**

## Why

Vector similarity returns chunks that look alike, not the right answer. A
semantic layer adds **entities** (Customer, TradingStrategy, LocalizationProject)
and **metrics** (lifetime_value, sharpe_ratio, terminology_consistency_score)
on top of raw vector data so agents can answer business questions, not just
retrieve similar strings.

## Catalog (10 entities)

| Entity | Key attributes | Metrics |
|--------|---------------|---------|
| Customer | id, scope, segment, signup_date | lifetime_value, churn_risk |
| TradingStrategy | id, scope, market, asset_class | sharpe_ratio, max_drawdown, win_rate |
| LocalizationProject | id, scope, source_lang, target_langs | terminology_consistency_score, throughput |
| Agent | id, scope, role, layer | success_rate, p95_latency_ms |
| Skill | id, name, version, capability_set | invocation_count, error_rate |
| Permission | id, scope, attenuation_chain | grant_count, revoke_count |
| MarketData | symbol, exchange, last_price | spread, volume_24h |
| Connector | id, type, source, last_heartbeat | uptime_pct, error_rate |
| DAOMember | address, scope, voting_power | participation_rate |
| AuditEvent | id, scope, agent_id, action, ts | cost_usd, governance_decision |

> **Note on tenant field.** The spec lists `tenant_id` for every entity. In
> `memory-core-mcp`, the corresponding column is `scope VARCHAR2(64)` (the
> existing tenant-isolation field used by `memories` / `memory_links`). The
> semantic-layer tables (`entities`, `entity_relationships`, `entity_aliases`)
> follow the same convention so a single RLS policy applies.

## Reference Implementation

Module: [`src/memory_core/semantic.py`](../src/memory_core/semantic.py).

- `Entity` — frozen dataclass: name, schema (dict), metrics (list),
  resolver (callable), required (tuple), description (str).
- `SemanticLayer` — in-process catalog. Methods: `define_entity`,
  `register_catalog`, `translate_to_sql`, `resolve`.
- `KnowledgeGraph(cfg)` — relational facade over the four Oracle tables.
  Methods: `add_entity`, `add_relationship`, `search`, `neighbors`,
  `resolve_term`.

The default catalog is built at import time from
`_build_catalog()` and exposed as `CATALOG` (10 entries).

## Backing Tables (DDL)

[`db/05_semantic_layer.sql`](../db/05_semantic_layer.sql) is applied manually
by the DBA — same convention as the other Oracle DDL files in `db/`.

| Table | Purpose |
|-------|---------|
| `entities` | Typed entity rows + JSON attribute bag |
| `entity_relationships` | Weighted typed edges (idempotent on `(from_id, to_id, relation)`) |
| `entity_aliases` | Locale-aware alias lookup for `resolve_term` |
| `entity_embeddings` | 384-dim `VECTOR` for hybrid retrieval |

## MCP Tools

Registered in [`src/memory_core/server.py`](../src/memory_core/server.py):

| Tool | Purpose |
|------|---------|
| `search_memory(query, entity_type?, scope?, limit?)` | Hybrid vector + alias retrieval |
| `resolve_metric(entity, metric)` | Catalog lookup (no DB hit) |
| `add_entity(entity_type, attributes, display?, scope?, aliases?, locale?)` | Persist entity + optional aliases + auto-embedding |
| `add_relationship(from_id, to_id, relation, weight?, scope?)` | Create a typed weighted edge (idempotent) |
| `query_graph(entity_id, relation?, max_hops?, limit?, scope?)` | Graph traversal, 1..3 hops |

Unknown `entity_type` values are rejected at the tool layer with a clean
`ValueError` so LLM clients can recover without an opaque Oracle traceback.

## Hybrid Retrieval

`KnowledgeGraph.search` picks the cheapest path that produces results:

1. **`indb` mode** (Oracle ONNX `ZMEM_EMBED` loaded) — uses
   `VECTOR_EMBEDDING(...)` for the query, then unions with `entity_aliases`
   `LOWER(alias) = LOWER(query)`.
2. **`client` mode** (fastembed local) — same shape but the query vector is
   computed via `embedder.embed(query)` on the Python side.
3. **`none` mode** (no embedding model) — falls back to `LIKE` over
   `display` and `attributes`.

## Adoption Checklist (memory-core-mcp slice)

- [x] Define entity catalog (10 entities)
- [x] Implement `SemanticLayer.define_entity`, `translate_to_sql`, `resolve`
- [x] Implement `KnowledgeGraph` with `add_entity`, `add_relationship`,
      `search`, `neighbors`, `resolve_term`
- [x] Expose 5 MCP tools on `memory-core-mcp`
- [x] Add unit tests (`tests/test_semantic.py`, 19 tests)
- [x] Add DB integration test (`tests/integration/test_semantic_integration.py`,
      gated on Oracle credentials)
- [ ] Migrate `ai-memory-vault` from vector mirror to `KnowledgeGraph` backend
      (separate ticket)
- [ ] Wire `chainlingo`, `glossaryGuard`, `tokenVoice` (separate tickets)

## Open Questions

- **Graph backend** — Relational tables in Oracle for PoC. Migration to
  Neo4j / SurrealDB / Oracle PGX is deferred until query patterns justify it.
- **Metric storage** — Co-located with entities in this PoC; a separate
  metrics store is a future optimization.
- **Resolver authoring** — Python callables returning SQL fragments. No
  DSL in v1.
