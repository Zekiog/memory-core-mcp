#!/usr/bin/env python3
"""Live integration test for the ZIONOS L2 semantic layer.

Runs only when Oracle credentials + the semantic-layer migration have been
applied (otherwise pytest.skip() with a clear message). Self-cleans the
rows it inserts so it is safe to re-run.
"""
from __future__ import annotations

import os
import sys
import uuid

import pytest

from memory_core import db, semantic
from memory_core.config import Config


def _reachable() -> bool:
    """Skip when we don't have a real Oracle target available."""
    if os.getenv("ZIONOS_SEMANTIC_SKIP") == "1":
        return False
    try:
        cfg = Config.load()
    except Exception:
        return False
    if not cfg.dsn or not cfg.user:
        return False
    db.init_pool(cfg)
    return True


pytestmark = pytest.mark.skipif(
    not _reachable(),
    reason="Oracle ADB credentials not configured (set DSN + user) or ZIONOS_SEMANTIC_SKIP=1",
)


@pytest.fixture(scope="module")
def cfg():
    """Lazy Config load so collection never fails when Oracle creds are absent."""
    return Config.load()


def _cleanup(scope: str, *ids: str) -> None:
    with db.conn() as c, c.cursor() as cur:
        for ent_id in ids:
            cur.execute("DELETE FROM entity_embeddings WHERE entity_id = :id", id=ent_id)
            cur.execute("DELETE FROM entity_relationships WHERE scope = :s AND (from_id = :id OR to_id = :id)",
                        s=scope, id=ent_id)
            cur.execute("DELETE FROM entity_aliases WHERE scope = :s AND canonical_id = :id",
                        s=scope, id=ent_id)
            cur.execute("DELETE FROM entities WHERE scope = :s AND id = :id",
                        s=scope, id=ent_id)
        c.commit()


def test_round_trip_add_entity_search_query_graph(cfg):
    scope = f"zionos-it-{uuid.uuid4().hex[:8]}"
    kg = semantic.KnowledgeGraph(cfg)
    inserted: list[str] = []
    try:
        cust1 = kg.add_entity(
            scope=scope, entity_type="Customer",
            display="Acme Corp",
            attributes={"segment": "enterprise", "signup_date": "2023-01-15"},
            aliases=["ACME", "Acme"],
            locale="en",
        )
        cust2 = kg.add_entity(
            scope=scope, entity_type="Customer",
            display="Globex",
            attributes={"segment": "smb", "signup_date": "2024-06-01"},
            aliases=["GBX"],
        )
        strat = kg.add_entity(
            scope=scope, entity_type="TradingStrategy",
            display="BTC-Momentum-v3",
            attributes={"market": "crypto", "asset_class": "BTC/USDT"},
        )
        inserted.extend([cust1, cust2, strat])

        kg.add_relationship(scope=scope, from_id=cust1, relation="owns", to_id=strat, weight=0.9)
        kg.add_relationship(scope=scope, from_id=cust2, relation="owns", to_id=strat, weight=0.4)
        kg.add_relationship(scope=scope, from_id=cust1, relation="manages", to_id=cust2)

        hits = kg.search(scope=scope, query="Acme", entity_type="Customer")
        assert any(h["id"] == cust1 for h in hits), hits

        resolved = kg.resolve_term(scope=scope, alias="GBX")
        assert resolved is not None
        assert resolved["id"] == cust2

        neighbors = kg.neighbors(scope=scope, entity_id=cust1, relation="owns", max_hops=1)
        assert any(n["id"] == strat for n in neighbors), neighbors

        wider = kg.neighbors(scope=scope, entity_id=cust1, max_hops=2)
        ids = {n["id"] for n in wider}
        assert cust2 in ids and strat in ids, wider

        layer = semantic.SemanticLayer()
        layer.register_catalog(semantic.CATALOG)
        frag = layer.translate_to_sql(
            {"entity": "Customer", "predicate": {"segment": "enterprise"}}
        )
        assert "entity_type = 'Customer'" in frag
    finally:
        _cleanup(scope, *inserted)


def test_resolve_metric_catalog_only():
    """Catalog resolution does not need the DB at all."""
    layer = semantic.SemanticLayer()
    layer.register_catalog(semantic.CATALOG)
    desc = layer.resolve("AuditEvent", "cost_usd")
    assert desc is not None
    assert desc["entity"] == "AuditEvent"
    assert desc["metric"] == "cost_usd"
    assert "agent_id" in desc["schema"]


def test_add_entity_unknown_type_is_rejected_at_tool_layer():
    """The MCP tool layer must raise on unknown entity types so LLM clients
    see a clean error rather than an opaque Oracle failure."""
    from memory_core.server import add_entity as add_entity_tool
    from memory_core import server as srv
    original = dict(srv._semantic._entities)
    srv._semantic._entities.pop("AuditEvent", None)
    try:
        with pytest.raises(ValueError, match="unknown entity_type"):
            add_entity_tool.fn(
                entity_type="AuditEvent",
                attributes={"id": "x", "scope": "y"},
            )
    finally:
        srv._semantic._entities.update(original)
