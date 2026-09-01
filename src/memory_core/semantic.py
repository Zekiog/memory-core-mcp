"""Semantic layer (ZIONOS Spec 1).

Adds typed entities, business-metric resolution, and a relational knowledge
graph on top of the existing memories / memory_links vector store.

Public surface:
    Entity, SemanticLayer, KnowledgeGraph, CATALOG

Spec: https://github.com/Zekiog/zion-os/blob/main/docs/semantic-layer.md
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable

from . import db
from .config import Config

# Type alias for business-predicate -> SQL translators. The dict argument is
# the parsed predicate; the return is a WHERE-clause fragment or full SQL.
PredicateResolver = Callable[[dict[str, Any]], str]


# ---------------------------------------------------------------------------
# Entity model
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Entity:
    """A typed semantic entity with attribute schema and metric names.

    `schema` maps attribute name -> expected JSON type ("string", "number",
    "iso_date", "enum:foo,bar", "json"). Free-form fields are allowed but
    flagged by `required` for the canonical contract.

    `metrics` is the metric catalog exposed via `resolve_metric` MCP tool.

    `resolver` translates a parsed business predicate into a SQL WHERE fragment
    using the entity's attribute names. Returning the empty string is a valid
    "no additional filtering" signal.
    """

    name: str
    schema: dict[str, str]
    metrics: list[str]
    resolver: PredicateResolver | None = None
    required: tuple[str, ...] = ()
    description: str = ""


# ---------------------------------------------------------------------------
# SemanticLayer
# ---------------------------------------------------------------------------

class SemanticLayer:
    """Catalog of entity types and their metric definitions.

    Lives in-process. Persistence of the catalog itself is implicit (every
    agent process re-registers at import time). The data — actual entity rows
    and relationships — lives in Oracle (entities / entity_relationships
    / entity_aliases / entity_embeddings).
    """

    def __init__(self) -> None:
        self._entities: dict[str, Entity] = {}

    # ---- catalog management -------------------------------------------------

    def define_entity(
        self,
        name: str,
        schema: dict[str, str],
        metrics: list[str],
        *,
        resolver: PredicateResolver | None = None,
        required: tuple[str, ...] = (),
        description: str = "",
    ) -> Entity:
        """Register or replace an entity type. Idempotent on `name`."""
        ent = Entity(
            name=name,
            schema=dict(schema),
            metrics=list(metrics),
            resolver=resolver,
            required=tuple(required),
            description=description,
        )
        self._entities[name] = ent
        return ent

    def register_catalog(self, catalog: dict[str, Entity]) -> None:
        for ent in catalog.values():
            self._entities[ent.name] = ent

    def get(self, name: str) -> Entity | None:
        return self._entities.get(name)

    def __contains__(self, name: object) -> bool:
        return name in self._entities

    def __iter__(self):
        return iter(self._entities.values())

    def __len__(self) -> int:
        return len(self._entities)

    # ---- query translation --------------------------------------------------

    def translate_to_sql(self, business_query: dict[str, Any]) -> str:
        """Translate a parsed business query into a SQL fragment.

        Input shape: {"entity": "Customer", "predicate": {...}}. Output is a
        `WHERE`-clause fragment ready to interpolate into a SELECT against
        `entities`. Raises KeyError if the entity is unknown so callers can
        surface a clean error to MCP clients.
        """
        entity_name = business_query.get("entity")
        if not entity_name:
            raise ValueError("business_query.entity is required")
        ent = self._entities[entity_name]
        predicate = business_query.get("predicate") or {}
        if not isinstance(predicate, dict):
            raise ValueError("business_query.predicate must be a dict")
        if ent.resolver is None:
            raise ValueError(
                f"entity {entity_name!r} has no predicate resolver; "
                "use a direct add_entity / search_memory call instead"
            )
        fragment = ent.resolver(predicate)
        return f"entity_type = '{entity_name}'" + (
            f" AND {fragment}" if fragment else ""
        )

    def resolve(self, entity: str, metric: str) -> dict[str, Any] | None:
        """Look up a metric definition. Returns the descriptor or None."""
        ent = self._entities.get(entity)
        if ent is None or metric not in ent.metrics:
            return None
        return {
            "entity": entity,
            "metric": metric,
            "schema": list(ent.schema.keys()),
            "required": list(ent.required),
            "description": ent.description,
        }


# ---------------------------------------------------------------------------
# KnowledgeGraph
# ---------------------------------------------------------------------------

class KnowledgeGraph:
    """Relational graph over `entities` + `entity_relationships` + `aliases`.

    Thin façade over `db.conn()`. All writes enforce `scope` from the caller's
    MCP context — there is no implicit tenant fallback. This is the only way
    to read or mutate the catalog data; the raw SQL lives here so the higher
    layers (MCP tools, REST handlers) don't have to know about Oracle.
    """

    def __init__(self, cfg: Config) -> None:
        self._cfg = cfg

    # ---- writes -------------------------------------------------------------

    def add_entity(
        self,
        *,
        scope: str,
        entity_type: str,
        attributes: dict[str, Any],
        display: str | None = None,
        aliases: list[str] | None = None,
        locale: str = "en",
    ) -> str:
        """Insert one entity + its aliases + its embedding.

        Returns the new entity id. The embedding is generated from
        `display + json.dumps(attributes, sort_keys=True)` so the vector
        reflects both the label and the full attribute bag.
        """
        attrs_json = json.dumps(attributes, sort_keys=True, default=str)
        display = display or attributes.get("name") or entity_type
        with db.conn() as c, c.cursor() as cur:
            out_id = cur.var(oracledb.STRING)
            mode = db.embed_mode(self._cfg)
            etext = f"{display}\n{attrs_json}"
            if mode == "indb":
                cur.execute(
                    f"""
                    INSERT INTO entities (scope, entity_type, attributes, display)
                    VALUES (:scope, :etype, :attrs, :disp)
                    RETURNING id INTO :rid
                    """,
                    scope=scope, etype=entity_type, attrs=attrs_json,
                    disp=display, rid=out_id,
                )
                ent_id = out_id.getvalue()[0]
                cur.execute(
                    f"""
                    INSERT INTO entity_embeddings (entity_id, embedding)
                    VALUES (:id, VECTOR_EMBEDDING({self._cfg.embed_model}
                                                   USING :t AS data))
                    """,
                    id=ent_id, t=etext,
                )
            else:
                # client / none — embedding optional
                cur.execute(
                    """
                    INSERT INTO entities (scope, entity_type, attributes, display)
                    VALUES (:scope, :etype, :attrs, :disp)
                    RETURNING id INTO :rid
                    """,
                    scope=scope, etype=entity_type, attrs=attrs_json,
                    disp=display, rid=out_id,
                )
                ent_id = out_id.getvalue()[0]
                if mode == "client":
                    vec = embedder.embed(etext)
                    if vec is not None:
                        cur.execute(
                            "INSERT INTO entity_embeddings (entity_id, embedding) "
                            "VALUES (:id, :v)",
                            id=ent_id, v=vec,
                        )
            # aliases (idempotent on the (scope, locale, alias) constraint)
            for alias in aliases or []:
                cur.execute(
                    """
                    MERGE INTO entity_aliases a
                    USING (SELECT :s AS scope, :l AS locale, :al AS alias,
                                  :cid AS canonical_id, :et AS entity_type
                           FROM dual) x
                    ON (a.scope = x.scope AND a.locale = x.locale AND a.alias = x.alias)
                    WHEN NOT MATCHED THEN
                      INSERT (scope, locale, alias, canonical_id, entity_type)
                      VALUES (x.scope, x.locale, x.alias, x.canonical_id, x.entity_type)
                    """,
                    s=scope, l=locale, al=alias, cid=ent_id, et=entity_type,
                )
            c.commit()
        return ent_id

    def add_relationship(
        self,
        *,
        scope: str,
        from_id: str,
        relation: str,
        to_id: str,
        weight: float = 1.0,
    ) -> str:
        """Insert a typed weighted edge. Idempotent on (from_id, to_id, relation)."""
        with db.conn() as c, c.cursor() as cur:
            out_id = cur.var(oracledb.STRING)
            cur.execute(
                """
                MERGE INTO entity_relationships r
                USING (SELECT :s AS scope, :f AS from_id, :rel AS relation,
                              :t AS to_id, :w AS weight FROM dual) x
                ON (r.from_id = x.from_id AND r.to_id = x.to_id AND r.relation = x.relation)
                WHEN NOT MATCHED THEN
                  INSERT (scope, from_id, relation, to_id, weight)
                  VALUES (x.scope, x.from_id, x.relation, x.to_id, x.weight)
                WHEN MATCHED THEN
                  UPDATE SET r.weight = x.weight
                RETURNING id INTO :rid
                """,
                s=scope, f=from_id, rel=relation, t=to_id, w=weight, rid=out_id,
            )
            c.commit()
            return out_id.getvalue()[0]

    # ---- reads --------------------------------------------------------------

    def neighbors(
        self,
        *,
        scope: str,
        entity_id: str,
        relation: str | None = None,
        max_hops: int = 1,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Return entities reachable in 1..max_hops outgoing edges.

        Implemented as a recursive WITH; capped at 3 hops (the spec's
        TRAVERSE 1..3 envelope).
        """
        if not 1 <= max_hops <= 3:
            raise ValueError("max_hops must be in 1..3")
        hops_filter = "" if relation is None else "AND relation = :rel"
        with db.conn() as c, c.cursor() as cur:
            cur.execute(
                f"""
                WITH graph(node_id, depth, via_relation) AS (
                  SELECT id, 0, NULL FROM entities
                  WHERE id = :root AND scope = :scope
                  UNION ALL
                  SELECT r.to_id, g.depth + 1, r.relation
                  FROM graph g
                  JOIN entity_relationships r ON r.from_id = g.node_id
                  WHERE g.depth < :hops {hops_filter}
                )
                SELECT DISTINCT e.id, e.entity_type, e.display, e.attributes,
                       g.depth, g.via_relation
                FROM graph g
                JOIN entities e ON e.id = g.node_id
                WHERE g.depth > 0
                ORDER BY g.depth, e.display
                FETCH FIRST :lim ROWS ONLY
                """,
                root=entity_id, scope=scope, hops=max_hops,
                rel=relation, lim=limit,
            )
            return [_entity_row(r) for r in cur.fetchall()]

    def resolve_term(
        self,
        *,
        scope: str,
        alias: str,
        locale: str = "en",
    ) -> dict[str, Any] | None:
        """Map an alias (in a given locale) to its canonical entity."""
        with db.conn() as c, c.cursor() as cur:
            cur.execute(
                """
                SELECT e.id, e.entity_type, e.display, e.attributes
                FROM entity_aliases a
                JOIN entities e ON e.id = a.canonical_id
                WHERE a.scope = :scope AND a.locale = :loc
                  AND LOWER(a.alias) = LOWER(:alias)
                FETCH FIRST 1 ROW ONLY
                """,
                scope=scope, loc=locale, alias=alias,
            )
            row = cur.fetchone()
            return _entity_row(row) if row else None

    def search(
        self,
        *,
        scope: str,
        query: str,
        entity_type: str | None = None,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """Hybrid retrieval: vector similarity ∪ alias match.

        Falls back to LIKE on attributes when no embedding model is loaded.
        """
        with db.conn() as c, c.cursor() as cur:
            mode = db.embed_mode(self._cfg)
            where_extra = ""
            binds: dict[str, Any] = {
                "scope": scope, "lim": max(1, min(limit, 50)),
            }
            if entity_type:
                where_extra = " AND e.entity_type = :etype"
                binds["etype"] = entity_type
            if mode in ("indb", "client"):
                if mode == "indb":
                    qexpr = (
                        f"VECTOR_EMBEDDING({self._cfg.embed_model} "
                        f"USING :q AS data)"
                    )
                    binds["q"] = query
                else:
                    vec = embedder.embed(query)
                    if vec is None:
                        qexpr = ":q"; binds["q"] = query
                    else:
                        qexpr = ":q"; binds["q"] = vec
                cur.execute(
                    f"""
                    SELECT e.id, e.entity_type, e.display, e.attributes,
                           VECTOR_DISTANCE(em.embedding, {qexpr}, COSINE) AS distance,
                           1 AS via_vector
                    FROM entities e
                    JOIN entity_embeddings em ON em.entity_id = e.id
                    WHERE e.scope = :scope
                      AND em.embedding IS NOT NULL{where_extra}
                    UNION
                    SELECT e.id, e.entity_type, e.display, e.attributes,
                           0.0 AS distance, 0 AS via_vector
                    FROM entities e
                    JOIN entity_aliases a ON a.canonical_id = e.id
                    WHERE e.scope = :scope
                      AND LOWER(a.alias) = LOWER(:q_alias){where_extra}
                    ORDER BY via_vector DESC, distance ASC
                    FETCH FIRST :lim ROWS ONLY
                    """,
                    **binds, q_alias=query,
                )
            else:
                binds["q"] = f"%{query.lower()}%"
                cur.execute(
                    f"""
                    SELECT e.id, e.entity_type, e.display, e.attributes,
                           0.0 AS distance, 0 AS via_vector
                    FROM entities e
                    WHERE e.scope = :scope
                      AND (LOWER(e.display) LIKE :q
                           OR LOWER(e.attributes) LIKE :q){where_extra}
                    ORDER BY e.updated_at DESC
                    FETCH FIRST :lim ROWS ONLY
                    """,
                    **binds,
                )
            return [_entity_row(r) for r in cur.fetchall()]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _entity_row(row: tuple | None) -> dict[str, Any]:
    if row is None:
        return {}
    ent_id, etype, display, attrs = row[0], row[1], row[2], row[3]
    depth = row[4] if len(row) > 4 else None
    via_relation = row[5] if len(row) > 5 else None
    if isinstance(attrs, str):
        try:
            attrs = json.loads(attrs)
        except (ValueError, TypeError):
            pass
    out = {
        "id": ent_id, "entity_type": etype, "display": display,
        "attributes": attrs or {},
    }
    if depth is not None:
        out["depth"] = depth
    if via_relation is not None:
        out["via_relation"] = via_relation
    return out


# ---------------------------------------------------------------------------
# Catalog (Spec 1 — 10 entities)
# ---------------------------------------------------------------------------

# Importing these here (rather than at module top) keeps the module importable
# without an Oracle connection — only the runtime DB operations need the pool.
import oracledb  # noqa: E402
from . import embedder  # noqa: E402


def _resolver_for(entity: str) -> PredicateResolver:
    """Build a generic resolver that translates a flat equality predicate."""
    def _resolve(predicate: dict[str, Any]) -> str:
        if not predicate:
            return ""
        parts = []
        for k, v in predicate.items():
            if isinstance(v, str):
                parts.append(f"JSON_VALUE(attributes, '$.{k}') = '{v.replace(chr(39), chr(39)*2)}'")
            elif isinstance(v, (int, float)):
                parts.append(f"CAST(JSON_VALUE(attributes, '$.{k}') AS NUMBER) = {v}")
            else:
                parts.append(
                    f"JSON_VALUE(attributes, '$.{k}') = "
                    f"'{json.dumps(v, default=str).replace(chr(39), chr(39)*2)}'"
                )
        return " AND ".join(parts)
    _resolve.__name__ = f"_resolve_{entity}"
    return _resolve


def _build_catalog() -> dict[str, Entity]:
    """Build the 10-entity catalog from the ZIONOS spec."""
    raw: list[tuple[str, dict, list, tuple, str]] = [
        (
            "Customer", {"id": "string", "scope": "string", "segment": "enum:enterprise,smb,consumer",
                         "signup_date": "iso_date"},
            ["lifetime_value", "churn_risk"], ("display",),
            "Enterprise customer in a multi-tenant RLS partition.",
        ),
        (
            "TradingStrategy", {"id": "string", "scope": "string", "market": "enum:crypto,equity,fx",
                                "asset_class": "string"},
            ["sharpe_ratio", "max_drawdown", "win_rate"], ("display",),
            "Trading strategy managed by fincept-ai-ops.",
        ),
        (
            "LocalizationProject", {"id": "string", "scope": "string",
                                    "source_lang": "bcp47", "target_langs": "array:bcp47"},
            ["terminology_consistency_score", "throughput"], ("display",),
            "Localization project owned by chainlingo.",
        ),
        (
            "Agent", {"id": "string", "scope": "string", "role": "string", "layer": "string"},
            ["success_rate", "p95_latency_ms"], ("display",),
            "Runtime agent executing in any ZIONOS layer.",
        ),
        (
            "Skill", {"id": "string", "name": "string", "version": "semver",
                      "capability_set": "array:string"},
            ["invocation_count", "error_rate"], ("display",),
            "Callable skill attached to one or more agents.",
        ),
        (
            "Permission", {"id": "string", "scope": "string",
                           "attenuation_chain": "array:string"},
            ["grant_count", "revoke_count"], ("display",),
            "Capability attenuation chain (Biscuit or eBPF token-derived).",
        ),
        (
            "MarketData", {"symbol": "string", "exchange": "string",
                           "last_price": "number"},
            ["spread", "volume_24h"], ("display",),
            "Live market snapshot used by trading strategies.",
        ),
        (
            "Connector", {"id": "string", "type": "string", "source": "string",
                           "last_heartbeat": "iso_date"},
            ["uptime_pct", "error_rate"], ("display",),
            "External system connector with heartbeat / uptime metrics.",
        ),
        (
            "DAOMember", {"address": "address:eth", "scope": "string",
                          "voting_power": "number"},
            ["participation_rate"], ("display",),
            "DAO voting member used by tokenVoice governance.",
        ),
        (
            "AuditEvent", {"id": "string", "scope": "string", "agent_id": "string",
                           "action": "string", "ts": "iso_date"},
            ["cost_usd", "governance_decision"], ("display",),
            "Immutable audit event projected from public.event_log.",
        ),
    ]
    catalog: dict[str, Entity] = {}
    for name, schema, metrics, required, desc in raw:
        catalog[name] = Entity(
            name=name,
            schema=schema,
            metrics=metrics,
            resolver=_resolver_for(name),
            required=required,
            description=desc,
        )
    return catalog


CATALOG: dict[str, Entity] = _build_catalog()
