#!/usr/bin/env python3
"""Unit tests for the ZIONOS L2 semantic layer (no DB required).

Spec: https://github.com/Zekiog/zion-os/blob/main/docs/semantic-layer.md
"""
from __future__ import annotations

import pytest

from memory_core.semantic import (
    CATALOG,
    Entity,
    KnowledgeGraph,
    SemanticLayer,
)


# ---------------------------------------------------------------------------
# Catalog
# ---------------------------------------------------------------------------

def test_catalog_has_all_ten_entities():
    expected = {
        "Customer", "TradingStrategy", "LocalizationProject",
        "Agent", "Skill", "Permission", "MarketData",
        "Connector", "DAOMember", "AuditEvent",
    }
    assert expected == set(CATALOG.keys())


@pytest.mark.parametrize("name", sorted(CATALOG.keys()))
def test_every_catalog_entity_has_metrics_and_resolver(name):
    ent = CATALOG[name]
    assert isinstance(ent, Entity)
    assert ent.metrics, f"{name} has no metrics"
    assert ent.resolver is not None, f"{name} has no resolver"
    assert ent.schema, f"{name} has no schema"
    assert ent.description, f"{name} missing description"


# ---------------------------------------------------------------------------
# SemanticLayer
# ---------------------------------------------------------------------------

def test_define_entity_is_idempotent():
    sl = SemanticLayer()
    sl.define_entity(
        "Foo",
        schema={"id": "string"},
        metrics=["bar"],
        description="first",
    )
    sl.define_entity(
        "Foo",
        schema={"id": "string", "extra": "number"},
        metrics=["bar", "baz"],
        description="second",
    )
    assert len(sl) == 1
    ent = sl.get("Foo")
    assert "extra" in ent.schema
    assert "baz" in ent.metrics
    assert ent.description == "second"


def test_translate_to_sql_emits_entity_filter():
    sl = SemanticLayer()
    sl.register_catalog(CATALOG)
    frag = sl.translate_to_sql(
        {"entity": "Customer", "predicate": {"segment": "enterprise"}}
    )
    assert frag.startswith("entity_type = 'Customer'")
    assert "JSON_VALUE(attributes, '$.segment')" in frag
    assert "'enterprise'" in frag


def test_translate_to_sql_handles_numeric_predicate():
    sl = SemanticLayer()
    sl.register_catalog(CATALOG)
    frag = sl.translate_to_sql(
        {"entity": "TradingStrategy", "predicate": {"sharpe_ratio": 1.5}}
    )
    assert "CAST(JSON_VALUE(attributes, '$.sharpe_ratio') AS NUMBER) = 1.5" in frag


def test_translate_to_sql_empty_predicate_is_valid():
    sl = SemanticLayer()
    sl.register_catalog(CATALOG)
    frag = sl.translate_to_sql({"entity": "Agent", "predicate": {}})
    assert frag == "entity_type = 'Agent'"


def test_translate_to_sql_unknown_entity_raises():
    sl = SemanticLayer()
    sl.register_catalog(CATALOG)
    with pytest.raises(KeyError):
        sl.translate_to_sql({"entity": "NoSuchEntity"})


def test_resolve_returns_descriptor():
    sl = SemanticLayer()
    sl.register_catalog(CATALOG)
    desc = sl.resolve("Customer", "lifetime_value")
    assert desc is not None
    assert desc["entity"] == "Customer"
    assert desc["metric"] == "lifetime_value"
    assert "segment" in desc["schema"]


def test_resolve_unknown_metric_returns_none():
    sl = SemanticLayer()
    sl.register_catalog(CATALOG)
    assert sl.resolve("Customer", "no_such_metric") is None
    assert sl.resolve("NotAnEntity", "x") is None


# ---------------------------------------------------------------------------
# KnowledgeGraph (no DB — error path only)
# ---------------------------------------------------------------------------

def test_knowledge_graph_rejects_invalid_max_hops():
    """The spec caps hops at 3; KG must reject 0 and 4."""
    from memory_core.config import Config
    cfg = Config(
        dsn="", user="", password="", wallet_dir="", wallet_password=None,
        embed_model="ZMEM_EMBED", default_scope="default",
        vault_dir=None, vault_mirror=False,
    )
    kg = KnowledgeGraph(cfg)
    with pytest.raises(ValueError):
        kg.neighbors(scope="x", entity_id="y", max_hops=0)
    with pytest.raises(ValueError):
        kg.neighbors(scope="x", entity_id="y", max_hops=4)
