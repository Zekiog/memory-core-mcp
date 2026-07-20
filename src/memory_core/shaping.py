"""ZMEM-Q1 response shaping: opt-in max_tokens / chunk_size / fields.

Pure post-processing over search/recent result rows. With no options this is
an identity function, so default API behaviour is byte-for-byte unchanged.
All operations return new dicts; input rows are never mutated.
"""
from __future__ import annotations

_CHARS_PER_TOKEN = 4  # deterministic approximation; no tokenizer dependency


def approx_tokens(text: str) -> int:
    """Deterministic token estimate (ceil of chars/4)."""
    return (len(text) + _CHARS_PER_TOKEN - 1) // _CHARS_PER_TOKEN


def _split_paragraphs(body: str) -> list[str]:
    return [p for p in body.split("\n\n") if p]


def _chunk_record(rec: dict, chunk_size: int) -> dict:
    """Split body into ~chunk_size-token chunks on paragraph boundaries."""
    body = rec.get("body") or ""
    source_base = rec.get("vault_path") or rec.get("source") or rec.get("id", "")
    chunks: list[dict] = []
    current: list[str] = []
    current_tokens = 0
    for para in _split_paragraphs(body):
        para_tokens = approx_tokens(para)
        if current and current_tokens + para_tokens > chunk_size:
            chunks.append({"text": "\n\n".join(current)})
            current, current_tokens = [], 0
        current.append(para)
        current_tokens += para_tokens
    if current:
        chunks.append({"text": "\n\n".join(current)})
    for i, chunk in enumerate(chunks, start=1):
        chunk["source"] = f"{source_base}#c{i}"
    out = {k: v for k, v in rec.items() if k != "body"}
    out["chunks"] = chunks
    return out


def _cap_records(rows: list[dict], max_tokens: int) -> list[dict]:
    """Accumulate rows in order until the token budget is spent.

    The record that crosses the budget is tail-truncated and flagged with
    truncated=True; later records are dropped.
    """
    out: list[dict] = []
    remaining = max_tokens
    for rec in rows:
        body = rec.get("body") or ""
        cost = approx_tokens(body)
        if cost <= remaining:
            out.append(dict(rec))
            remaining -= cost
            continue
        if remaining > 0:
            clipped = dict(rec)
            clipped["body"] = body[: remaining * _CHARS_PER_TOKEN]
            clipped["truncated"] = True
            out.append(clipped)
        break
    return out


def _project(rec: dict, fields: list[str]) -> dict:
    allowed = set(fields) | {"truncated"}
    return {k: v for k, v in rec.items() if k in allowed}


def shape_results(
    rows: list[dict],
    *,
    max_tokens: int | None = None,
    chunk_size: int | None = None,
    fields: list[str] | None = None,
) -> list[dict]:
    """Apply opt-in shaping in order: budget cap -> chunking -> projection."""
    if max_tokens is None and chunk_size is None and fields is None:
        return rows
    shaped = list(rows)
    if max_tokens is not None:
        shaped = _cap_records(shaped, max_tokens)
    if chunk_size is not None:
        shaped = [_chunk_record(r, chunk_size) for r in shaped]
    if fields is not None:
        shaped = [_project(r, fields) for r in shaped]
    return shaped
