-- ============================================================================
-- Memory-core Oracle 23ai migration 05 — Semantic Layer (Spec 1)
-- Source spec: https://github.com/Zekiog/zion-os/blob/main/docs/semantic-layer.md
-- ----------------------------------------------------------------------------
-- Run as ZMEM on zmemory-adb (Oracle 23ai). Idempotent: re-runnable.
-- Adds 4 tables: entities, entity_relationships, entity_aliases,
--                entity_embeddings (VECTOR(384, FLOAT32)).
-- Tenant isolation: scope (matches existing memories / memory_links).
-- Vector dim matches memories.embedding and the ZMEM_EMBED ONNX model.
-- ============================================================================

-- ---------------------------------------------------------------------------
-- 1. entities — the typed rows in the semantic catalog.
--    attributes is a JSON document constrained to the registered schema.
-- ---------------------------------------------------------------------------
DECLARE
  e_table_exists EXCEPTION;
  PRAGMA EXCEPTION_INIT(e_table_exists, -955);
BEGIN
  EXECUTE IMMEDIATE q'[
    CREATE TABLE entities (
      id          VARCHAR2(36)  DEFAULT SYS_GUID()  PRIMARY KEY,
      scope       VARCHAR2(64)  DEFAULT 'global'    NOT NULL,
      entity_type VARCHAR2(64)                       NOT NULL,
      attributes  JSON                              NOT NULL,
      display     VARCHAR2(512),                                  -- canonical label
      created_at  TIMESTAMP WITH TIME ZONE DEFAULT SYSTIMESTAMP NOT NULL,
      updated_at  TIMESTAMP WITH TIME ZONE DEFAULT SYSTIMESTAMP NOT NULL,
      CONSTRAINT entities_scope_type_uq UNIQUE (scope, entity_type, display)
    )
  ]';
EXCEPTION WHEN e_table_exists THEN NULL;
END;
/

CREATE INDEX entities_scope_type_idx     ON entities (scope, entity_type);
CREATE INDEX entities_scope_updated_idx  ON entities (scope, updated_at DESC);

COMMENT ON TABLE entities IS
  'Semantic catalog rows. attributes conforms to the schema registered in memory_core.semantic.CATALOG.';

-- ---------------------------------------------------------------------------
-- 2. entity_relationships — typed edges in the knowledge graph.
--    from_id / to_id reference entities(id). Self-loops permitted.
-- ---------------------------------------------------------------------------
DECLARE
  e_table_exists EXCEPTION;
  PRAGMA EXCEPTION_INIT(e_table_exists, -955);
BEGIN
  EXECUTE IMMEDIATE q'[
    CREATE TABLE entity_relationships (
      id         VARCHAR2(36)  DEFAULT SYS_GUID()  PRIMARY KEY,
      scope      VARCHAR2(64)  DEFAULT 'global'    NOT NULL,
      from_id    VARCHAR2(36)                       NOT NULL,
      relation   VARCHAR2(64)                       NOT NULL,
      to_id      VARCHAR2(36)                       NOT NULL,
      weight     BINARY_DOUBLE  DEFAULT 1.0        NOT NULL,
      created_at TIMESTAMP WITH TIME ZONE DEFAULT SYSTIMESTAMP NOT NULL,
      CONSTRAINT entity_rels_pk  PRIMARY KEY (from_id, to_id, relation),
      CONSTRAINT entity_rels_from FOREIGN KEY (from_id) REFERENCES entities(id) ON DELETE CASCADE,
      CONSTRAINT entity_rels_to   FOREIGN KEY (to_id)   REFERENCES entities(id) ON DELETE CASCADE
    )
  ]';
EXCEPTION WHEN e_table_exists THEN NULL;
END;
/

CREATE INDEX entity_rels_scope_idx       ON entity_relationships (scope, from_id);
CREATE INDEX entity_rels_relation_idx    ON entity_relationships (relation, from_id);

COMMENT ON TABLE entity_relationships IS
  'Directed weighted edges. Weight is the resolver confidence (1.0 = asserted by ZPM governance loop).';

-- ---------------------------------------------------------------------------
-- 3. entity_aliases — multilingual term resolution for resolve_term.
--    canonical_id references entities(id). locale is BCP-47 ('en', 'tr', ...).
-- ---------------------------------------------------------------------------
DECLARE
  e_table_exists EXCEPTION;
  PRAGMA EXCEPTION_INIT(e_table_exists, -955);
BEGIN
  EXECUTE IMMEDIATE q'[
    CREATE TABLE entity_aliases (
      id           VARCHAR2(36)  DEFAULT SYS_GUID()  PRIMARY KEY,
      scope        VARCHAR2(64)  DEFAULT 'global'    NOT NULL,
      entity_type  VARCHAR2(64)                       NOT NULL,
      alias        VARCHAR2(512)                      NOT NULL,
      canonical_id VARCHAR2(36)                       NOT NULL,
      locale       VARCHAR2(16)  DEFAULT 'en'        NOT NULL,
      created_at   TIMESTAMP WITH TIME ZONE DEFAULT SYSTIMESTAMP NOT NULL,
      CONSTRAINT entity_aliases_uq UNIQUE (scope, locale, alias),
      CONSTRAINT entity_aliases_fk FOREIGN KEY (canonical_id) REFERENCES entities(id) ON DELETE CASCADE
    )
  ]';
EXCEPTION WHEN e_table_exists THEN NULL;
END;
/

CREATE INDEX entity_aliases_lookup_idx ON entity_aliases (scope, locale, alias);
CREATE INDEX entity_aliases_canon_idx  ON entity_aliases (canonical_id);

COMMENT ON TABLE entity_aliases IS
  'Term resolution for resolve_term. Powers chainlingo + glossaryGuard terminology graph.';

-- ---------------------------------------------------------------------------
-- 4. entity_embeddings — 384-dim float vectors for hybrid search.
--    Same vector space as memories.embedding (ZMEM_EMBED ONNX model).
--    One embedding per entity (the canonical display + attributes hash).
-- ---------------------------------------------------------------------------
DECLARE
  e_table_exists EXCEPTION;
  PRAGMA EXCEPTION_INIT(e_table_exists, -955);
BEGIN
  EXECUTE IMMEDIATE q'[
    CREATE TABLE entity_embeddings (
      entity_id VARCHAR2(36) PRIMARY KEY,
      embedding VECTOR(384, FLOAT32) NOT NULL,
      generated_at TIMESTAMP WITH TIME ZONE DEFAULT SYSTIMESTAMP NOT NULL,
      CONSTRAINT entity_embeddings_fk FOREIGN KEY (entity_id) REFERENCES entities(id) ON DELETE CASCADE
    )
  ]';
EXCEPTION WHEN e_table_exists THEN NULL;
END;
/

-- IVF neighbor-partitions index, matching memories_vec_idx style. Safe on free tier.
DECLARE
  e_idx_exists EXCEPTION;
  PRAGMA EXCEPTION_INIT(e_idx_exists, -20008);
BEGIN
  EXECUTE IMMEDIATE q'[
    CREATE VECTOR INDEX entity_embeddings_vec_idx
      ON entity_embeddings (embedding)
      ORGANIZATION NEIGHBOR PARTITIONS
      DISTANCE COSINE
      WITH TARGET ACCURACY 90
  ]';
EXCEPTION WHEN e_idx_exists THEN NULL;
END;
/

COMMENT ON TABLE entity_embeddings IS
  'Per-entity 384-dim vector. Cosine-searched by search_memory (hybrid with aliases).';
