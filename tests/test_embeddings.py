"""Unit tests for utils/embeddings.py.

Tests are split into two groups:
- Always-run: logic that does not require the fastembed model (load_emb, is_available, etc.)
- fastembed-required: marked with ``requires_fastembed``; skipped when the
  package is absent or the model has not been downloaded yet.
"""
from __future__ import annotations

import numpy as np
import pytest
from pathlib import Path

from utils.embeddings import (
    _DIM,
    DEDUP_THRESHOLD,
    SEARCH_THRESHOLD,
    SEMANTIC_FLOOR,
    cosine_scores,
    is_available,
    load_emb,
    save_emb,
)

requires_fastembed = pytest.mark.skipif(
    not is_available(), reason="fastembed not installed"
)


# ── Constants sanity ──────────────────────────────────────────────────────────

def test_dim_is_768() -> None:
    assert _DIM == 768


def test_thresholds_in_range() -> None:
    assert 0 < SEMANTIC_FLOOR < SEARCH_THRESHOLD < DEDUP_THRESHOLD < 1.0


# ── load_emb / save_emb ───────────────────────────────────────────────────────

def test_load_emb_missing_file(tmp_path: Path) -> None:
    assert load_emb(tmp_path / "missing.npy", 5) is None


def test_load_emb_rejects_wrong_row_count(tmp_path: Path) -> None:
    arr = np.ones((3, _DIM), dtype=np.float32)
    p = tmp_path / "emb.npy"
    np.save(str(p), arr)
    assert load_emb(p, 5) is None  # 3 rows != 5


def test_load_emb_rejects_wrong_dim(tmp_path: Path) -> None:
    """Stale 384-dim matrices must be rejected, not silently loaded."""
    arr = np.ones((5, 384), dtype=np.float32)  # old BGE-small dimension
    p = tmp_path / "emb.npy"
    np.save(str(p), arr)
    assert load_emb(p, 5) is None  # shape (5, 384) != (5, 768)


def test_load_emb_accepts_correct_shape(tmp_path: Path) -> None:
    arr = np.ones((4, _DIM), dtype=np.float32)
    p = tmp_path / "emb.npy"
    save_emb(p, arr)
    loaded = load_emb(p, 4)
    assert loaded is not None
    assert loaded.shape == (4, _DIM)


def test_save_emb_creates_parent_dirs(tmp_path: Path) -> None:
    p = tmp_path / "deep" / "dir" / "emb.npy"
    arr = np.zeros((2, _DIM), dtype=np.float32)
    save_emb(p, arr)
    assert p.exists()


# ── cosine_scores ─────────────────────────────────────────────────────────────

def test_cosine_scores_empty_corpus() -> None:
    q = np.array([1.0, 0.0], dtype=np.float32)
    result = cosine_scores(q, np.zeros((0, 2), dtype=np.float32))
    assert result.shape == (0,)


def test_cosine_scores_identical_vectors() -> None:
    v = np.array([1.0, 0.0, 0.0], dtype=np.float32)
    scores = cosine_scores(v, v.reshape(1, -1))
    assert abs(float(scores[0]) - 1.0) < 1e-5


def test_cosine_scores_orthogonal_vectors() -> None:
    q = np.array([1.0, 0.0], dtype=np.float32)
    doc = np.array([[0.0, 1.0]], dtype=np.float32)
    scores = cosine_scores(q, doc)
    assert abs(float(scores[0])) < 1e-5


# ── encode_query / encode_document (require fastembed) ────────────────────────

@requires_fastembed
def test_encode_query_shape() -> None:
    from utils.embeddings import encode_query
    result = encode_query(["test query about authentication"])
    assert result is not None
    assert result.shape == (1, _DIM)


@requires_fastembed
def test_encode_document_shape() -> None:
    from utils.embeddings import encode_document
    result = encode_document(["JWT token expiry should be short"])
    assert result is not None
    assert result.shape == (1, _DIM)


@requires_fastembed
def test_encode_batch_shape() -> None:
    from utils.embeddings import encode_document
    texts = ["first pattern", "second pattern", "third pattern"]
    result = encode_document(texts)
    assert result is not None
    assert result.shape == (3, _DIM)


@requires_fastembed
def test_encode_empty_list_returns_zero_matrix() -> None:
    from utils.embeddings import encode_document
    result = encode_document([])
    assert result is not None
    assert result.shape == (0, _DIM)


@requires_fastembed
def test_vectors_are_normalized() -> None:
    from utils.embeddings import encode_document
    embs = encode_document(["some document text for normalization check"])
    assert embs is not None
    norm = float(np.linalg.norm(embs[0]))
    assert abs(norm - 1.0) < 1e-5


@requires_fastembed
def test_query_and_document_share_same_dim() -> None:
    from utils.embeddings import encode_query, encode_document
    q = encode_query(["what is token expiry"])
    d = encode_document(["JWT tokens expire after a configured TTL"])
    assert q is not None and d is not None
    assert q.shape[1] == d.shape[1] == _DIM
