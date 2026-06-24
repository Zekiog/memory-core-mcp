"""Oracle ADB 23ai access layer (thin mode, mTLS wallet).

Embeddings are generated in-DB via VECTOR_EMBEDDING when the ONNX model is
present; otherwise the store degrades gracefully to keyword search.
"""
from __future__ import annotations

import array
import json
from contextlib import contextmanager
from typing import Any, Iterable

import oracledb

from . import embedder
from .config import Config

_pool: oracledb.ConnectionPool | None = None
_indb_embed: bool | None = None  # in-DB ONNX VECTOR_EMBEDDING usable?


def init_pool(cfg: Config) -> None:
    global _pool
    if _pool is not None:
        return
    _pool = oracledb.create_pool(
        user=cfg.user,
        password=cfg.password,
        dsn=cfg.dsn,
        config_dir=cfg.wallet_dir,
        wallet_location=cfg.wallet_dir,
        wallet_password=cfg.wallet_password,
        min=1,
        max=4,
        increment=1,
    )


@contextmanager
def conn():
    assert _pool is not None, "pool not initialized"
    c = _pool.acquire()
    try:
        yield c
    finally:
        _pool.release(c)


def _indb_available(cfg: Config) -> bool:
    """Cache whether the in-DB ONNX embedding model is loaded and usable."""
    global _indb_embed
    if _indb_embed is not None:
        return _indb_embed
    try:
        with conn() as c, c.cursor() as cur:
            cur.execute(
                f"SELECT VECTOR_EMBEDDING({cfg.embed_model} USING :t AS data) FROM dual",
                t="probe",
            )
            cur.fetchone()
        _indb_embed = True
    except oracledb.DatabaseError:
        _indb_embed = False
    return _indb_embed


def embed_mode(cfg: Config) -> str:
    """Active embedding strategy: 'indb' | 'client' | 'none'."""
    if _indb_available(cfg):
        return "indb"
    if embedder.available():
        return "client"
    return "none"


def embeddings_available(cfg: Config) -> bool:
    return embed_mode(cfg) != "none"


def _to_vector(value: Any) -> array.array | None:
    if value is None:
        return None
    if isinstance(value, array.array):
        return value
    return array.array("f", list(value))


def add_memory(cfg: Config, *, body: str, title: str | None, kind: str,
               scope: str, tags: dict | None, source: str,
               vault_path: str | None) -> str:
    mode = embed_mode(cfg)
    etext = (title + "\n" + body) if title else body
    tags_json = json.dumps(tags) if tags else None
    with conn() as c, c.cursor() as cur:
        out_id = cur.var(oracledb.STRING)
        if mode == "indb":
            cur.execute(
                f"""
                INSERT INTO memories (scope, kind, title, body, tags, source, vault_path, embedding)
                VALUES (:scope, :kind, :title, :body, :tags, :source, :vpath,
                        VECTOR_EMBEDDING({cfg.embed_model} USING :etext AS data))
                RETURNING id INTO :rid
                """,
                scope=scope, kind=kind, title=title, body=body, tags=tags_json,
                source=source, vpath=vault_path, etext=etext, rid=out_id,
            )
        elif mode == "client":
            cur.execute(
                """
                INSERT INTO memories (scope, kind, title, body, tags, source, vault_path, embedding)
                VALUES (:scope, :kind, :title, :body, :tags, :source, :vpath, :evec)
                RETURNING id INTO :rid
                """,
                scope=scope, kind=kind, title=title, body=body, tags=tags_json,
                source=source, vpath=vault_path, evec=embedder.embed(etext),
                rid=out_id,
            )
        else:
            cur.execute(
                """
                INSERT INTO memories (scope, kind, title, body, tags, source, vault_path)
                VALUES (:scope, :kind, :title, :body, :tags, :source, :vpath)
                RETURNING id INTO :rid
                """,
                scope=scope, kind=kind, title=title, body=body, tags=tags_json,
                source=source, vpath=vault_path, rid=out_id,
            )
        c.commit()
        return out_id.getvalue()[0]


def upsert_memory_by_source(cfg: Config, *, scope: str, source: str,
                             title: str | None, body: str, kind: str,
                             tags: dict | None) -> tuple[str, bool]:
    """Insert, or update in place if a row with this (scope, source) exists.

    Used by content-sync automations (e.g. the n8n workflow catalog, Task 12)
    where re-running the sync must update existing rows, not duplicate them.
    Returns (id, created) where created is True only on first insert.
    """
    mode = embed_mode(cfg)
    etext = (title + "\n" + body) if title else body
    tags_json = json.dumps(tags) if tags else None
    with conn() as c, c.cursor() as cur:
        cur.execute(
            "SELECT id FROM memories WHERE scope = :scope AND source = :source",
            scope=scope, source=source,
        )
        row = cur.fetchone()
        if row is None:
            mem_id = None
        else:
            (mem_id,) = row
        if mem_id is not None:
            if mode == "indb":
                cur.execute(
                    f"""
                    UPDATE memories SET title = :title, body = :body, tags = :tags,
                           embedding = VECTOR_EMBEDDING({cfg.embed_model} USING :etext AS data),
                           updated_at = SYSTIMESTAMP
                    WHERE id = :id
                    """,
                    title=title, body=body, tags=tags_json, etext=etext, id=mem_id,
                )
            elif mode == "client":
                cur.execute(
                    """
                    UPDATE memories SET title = :title, body = :body, tags = :tags,
                           embedding = :evec, updated_at = SYSTIMESTAMP
                    WHERE id = :id
                    """,
                    title=title, body=body, tags=tags_json,
                    evec=embedder.embed(etext), id=mem_id,
                )
            else:
                cur.execute(
                    """
                    UPDATE memories SET title = :title, body = :body, tags = :tags,
                           updated_at = SYSTIMESTAMP
                    WHERE id = :id
                    """,
                    title=title, body=body, tags=tags_json, id=mem_id,
                )
            c.commit()
            return mem_id, False
    new_id = add_memory(cfg, body=body, title=title, kind=kind, scope=scope,
                         tags=tags, source=source, vault_path=None)
    return new_id, True


def _row_to_dict(cur, row) -> dict:
    cols = [d[0].lower() for d in cur.description]
    rec = dict(zip(cols, row))
    for k in ("body", "tags"):
        v = rec.get(k)
        if isinstance(v, oracledb.LOB):
            rec[k] = v.read()
    if isinstance(rec.get("tags"), str):
        try:
            rec["tags"] = json.loads(rec["tags"])
        except (ValueError, TypeError):
            pass
    for k in ("created_at", "updated_at"):
        if rec.get(k) is not None:
            rec[k] = rec[k].isoformat()
    rec.pop("embedding", None)
    return rec


_SELECT = ("id, scope, kind, title, body, tags, source, vault_path, "
           "created_at, updated_at")


def search(cfg: Config, *, query: str, scope: str | None, kind: str | None,
           limit: int) -> list[dict]:
    where = []
    binds: dict[str, Any] = {"k": limit}
    if scope:
        where.append("scope = :scope"); binds["scope"] = scope
    if kind:
        where.append("kind = :kind"); binds["kind"] = kind
    filt = (" AND " + " AND ".join(where)) if where else ""
    mode = embed_mode(cfg)
    with conn() as c, c.cursor() as cur:
        if mode in ("indb", "client"):
            if mode == "indb":
                qexpr = f"VECTOR_EMBEDDING({cfg.embed_model} USING :q AS data)"
                binds["q"] = query
            else:
                qexpr = ":q"
                binds["q"] = embedder.embed(query)
            cur.execute(
                f"""
                SELECT {_SELECT},
                       VECTOR_DISTANCE(embedding, {qexpr}, COSINE) AS distance
                FROM memories
                WHERE embedding IS NOT NULL{filt}
                ORDER BY distance
                FETCH FIRST :k ROWS ONLY
                """,
                **binds,
            )
        else:
            binds["q"] = f"%{query.lower()}%"
            cur.execute(
                f"""
                SELECT {_SELECT}
                FROM memories
                WHERE (LOWER(title) LIKE :q OR LOWER(DBMS_LOB.SUBSTR(body, 3900, 1)) LIKE :q){filt}
                ORDER BY updated_at DESC
                FETCH FIRST :k ROWS ONLY
                """,
                **binds,
            )
        rows = cur.fetchall()
        return [_row_to_dict(cur, r) for r in rows]


def get(cfg: Config, mem_id: str) -> dict | None:
    with conn() as c, c.cursor() as cur:
        cur.execute(f"SELECT {_SELECT} FROM memories WHERE id = :id", id=mem_id)
        row = cur.fetchone()
        if row is None:
            return None
        rec = _row_to_dict(cur, row)
        cur.execute(
            "SELECT dst_id, rel FROM memory_links WHERE src_id = :id",
            id=mem_id,
        )
        rec["links"] = [{"dst_id": d, "rel": r} for d, r in cur.fetchall()]
        return rec


def recent(cfg: Config, *, scope: str | None, kind: str | None,
           limit: int) -> list[dict]:
    where = []
    binds: dict[str, Any] = {"k": limit}
    if scope:
        where.append("scope = :scope"); binds["scope"] = scope
    if kind:
        where.append("kind = :kind"); binds["kind"] = kind
    filt = (" WHERE " + " AND ".join(where)) if where else ""
    with conn() as c, c.cursor() as cur:
        cur.execute(
            f"SELECT {_SELECT} FROM memories{filt} "
            f"ORDER BY updated_at DESC FETCH FIRST :k ROWS ONLY",
            **binds,
        )
        return [_row_to_dict(cur, r) for r in cur.fetchall()]


def link(cfg: Config, *, src_id: str, dst_id: str, rel: str) -> None:
    with conn() as c, c.cursor() as cur:
        cur.execute(
            "MERGE INTO memory_links l "
            "USING (SELECT :s AS src_id, :d AS dst_id, :r AS rel FROM dual) x "
            "ON (l.src_id = x.src_id AND l.dst_id = x.dst_id AND l.rel = x.rel) "
            "WHEN NOT MATCHED THEN INSERT (src_id, dst_id, rel) "
            "VALUES (x.src_id, x.dst_id, x.rel)",
            s=src_id, d=dst_id, r=rel,
        )
        c.commit()


def stats(cfg: Config) -> dict:
    with conn() as c, c.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*), "
            "SUM(CASE WHEN embedding IS NOT NULL THEN 1 ELSE 0 END) "
            "FROM memories"
        )
        total, embedded = cur.fetchone()
        cur.execute(
            "SELECT scope, kind, COUNT(*) FROM memories GROUP BY scope, kind "
            "ORDER BY COUNT(*) DESC"
        )
        breakdown = [
            {"scope": s, "kind": k, "count": n} for s, k, n in cur.fetchall()
        ]
        return {
            "total": total,
            "embedded": embedded,
            "embed_mode": embed_mode(cfg),
            "breakdown": breakdown,
        }


def backfill_embeddings(cfg: Config, batch: int = 200) -> int:
    """Compute embeddings for rows where embedding IS NULL. Returns count."""
    mode = embed_mode(cfg)
    if mode == "none":
        return 0
    done = 0
    with conn() as c, c.cursor() as cur:
        cur.execute(
            "SELECT id, title, body FROM memories WHERE embedding IS NULL "
            "FETCH FIRST :n ROWS ONLY", n=batch,
        )
        rows = cur.fetchall()
        for mem_id, title, body in rows:
            text = body.read() if isinstance(body, oracledb.LOB) else body
            if title:
                text = title + "\n" + text
            if mode == "indb":
                cur.execute(
                    f"UPDATE memories SET embedding = "
                    f"VECTOR_EMBEDDING({cfg.embed_model} USING :t AS data) "
                    f"WHERE id = :id", t=text, id=mem_id,
                )
            else:
                cur.execute(
                    "UPDATE memories SET embedding = :v WHERE id = :id",
                    v=embedder.embed(text), id=mem_id,
                )
            done += 1
        c.commit()
    return done
