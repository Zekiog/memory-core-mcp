"""ZMEM-Q1: response shaping — max_tokens / chunk_size / fields (opt-in, golden-safe)."""
from memory_core.shaping import approx_tokens, shape_results


def _rows():
    return [
        {"id": "a", "title": "T1", "body": "para one.\n\npara two is here.\n\npara three!",
         "vault_path": "notes/a.md", "scope": "s", "tags": ["x"]},
        {"id": "b", "title": "T2", "body": "B " * 400,  # ~800 chars
         "vault_path": "notes/b.md", "scope": "s", "tags": []},
    ]


# --- golden: parametresiz çağrı davranışı DEĞİŞTİRMEZ ---

def test_noop_without_params_returns_same_objects():
    rows = _rows()
    assert shape_results(rows) is rows


# --- max_tokens ---

def test_max_tokens_caps_total_budget():
    rows = _rows()
    out = shape_results(rows, max_tokens=approx_tokens(rows[0]["body"]) + 5)
    total = sum(approx_tokens(r["body"]) for r in out)
    assert total <= approx_tokens(rows[0]["body"]) + 5
    assert out[0]["id"] == "a"  # sıra korunur, ilk kayıt tam


def test_max_tokens_truncates_tail_record_and_flags_it():
    rows = _rows()
    budget = approx_tokens(rows[0]["body"]) + 20
    out = shape_results(rows, max_tokens=budget)
    assert len(out) == 2
    assert out[1]["truncated"] is True
    assert len(out[1]["body"]) < len(rows[1]["body"])
    assert "truncated" not in out[0]  # tam sığan kayıt işaretlenmez


def test_max_tokens_never_mutates_input():
    rows = _rows()
    body_before = rows[1]["body"]
    shape_results(rows, max_tokens=10)
    assert rows[1]["body"] == body_before


# --- chunk_size ---

def test_chunk_size_splits_on_paragraph_boundaries():
    out = shape_results(_rows()[:1], chunk_size=5)
    chunks = out[0]["chunks"]
    assert [c["source"] for c in chunks] == [f"notes/a.md#c{i}" for i in range(1, len(chunks) + 1)]
    assert "".join(c["text"] for c in chunks).replace("\n\n", "") == \
        _rows()[0]["body"].replace("\n\n", "")
    # paragraf ortasından kesilmez: her chunk text'i orijinal paragrafların birleşimi
    for c in chunks:
        for para in c["text"].split("\n\n"):
            assert para in _rows()[0]["body"]


def test_chunking_removes_flat_body():
    out = shape_results(_rows()[:1], chunk_size=5)
    assert "body" not in out[0]


# --- fields projection ---

def test_fields_projection_keeps_only_requested():
    out = shape_results(_rows(), fields=["id", "title", "vault_path"])
    assert set(out[0].keys()) == {"id", "title", "vault_path"}


def test_fields_unknown_names_ignored():
    out = shape_results(_rows(), fields=["id", "nope"])
    assert set(out[0].keys()) == {"id"}


# --- kombinasyon ---

def test_fields_with_max_tokens_projection_after_cap():
    out = shape_results(_rows(), max_tokens=5, fields=["id", "body"])
    assert all(set(r.keys()) <= {"id", "body", "truncated"} for r in out)
