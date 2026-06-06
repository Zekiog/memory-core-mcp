-- Run as ZMEM on zmemory-adb (Oracle 23ai).
-- Canonical knowledge-data-memory store: relational + JSON tags + VECTOR(384).

CREATE TABLE memories (
  id          VARCHAR2(36)  DEFAULT SYS_GUID()  PRIMARY KEY,
  scope       VARCHAR2(64)  DEFAULT 'global'    NOT NULL,   -- user/agent/project bucket
  kind        VARCHAR2(32)  DEFAULT 'fact'      NOT NULL,   -- capture|decision|fact|reference|project
  title       VARCHAR2(512),
  body        CLOB                              NOT NULL,
  tags        JSON,
  source      VARCHAR2(128) DEFAULT 'mcp',
  vault_path  VARCHAR2(512),                                -- backlink to markdown mirror
  embedding   VECTOR(384, FLOAT32),
  created_at  TIMESTAMP WITH TIME ZONE DEFAULT SYSTIMESTAMP NOT NULL,
  updated_at  TIMESTAMP WITH TIME ZONE DEFAULT SYSTIMESTAMP NOT NULL
);

CREATE INDEX memories_scope_kind_idx ON memories (scope, kind);
CREATE INDEX memories_updated_idx    ON memories (updated_at DESC);

-- Graph edges between memories (Obsidian-style [[links]] become rows).
CREATE TABLE memory_links (
  src_id     VARCHAR2(36) NOT NULL,
  dst_id     VARCHAR2(36) NOT NULL,
  rel        VARCHAR2(64) DEFAULT 'relates_to' NOT NULL,
  created_at TIMESTAMP WITH TIME ZONE DEFAULT SYSTIMESTAMP,
  CONSTRAINT memory_links_pk  PRIMARY KEY (src_id, dst_id, rel),
  CONSTRAINT memory_links_src FOREIGN KEY (src_id) REFERENCES memories(id) ON DELETE CASCADE,
  CONSTRAINT memory_links_dst FOREIGN KEY (dst_id) REFERENCES memories(id) ON DELETE CASCADE
);

-- Vector index (IVF / disk-based: safe on free tier's limited memory).
-- Create after some rows exist; exact search works without it for small sets.
-- CREATE VECTOR INDEX memories_vec_idx ON memories (embedding)
--   ORGANIZATION NEIGHBOR PARTITIONS
--   DISTANCE COSINE
--   WITH TARGET ACCURACY 90;
