"""Precision and regression tests for the nomic-embed-text-v1.5 embedding model.

These tests guard against model-quality regressions — specifically the
false-positive problem that existed with BGE-small-en-v1.5 (384-dim), where
unrelated patterns like "database migrations" scored 0.618 against "token
expiry" queries.

All tests require fastembed and are skipped when it is absent.
"""
from __future__ import annotations

import numpy as np
import pytest

from utils.embeddings import (
    DEDUP_THRESHOLD,
    SEARCH_THRESHOLD,
    cosine_scores,
    is_available,
)

pytestmark = pytest.mark.skipif(
    not is_available(), reason="fastembed not installed"
)


# ── Shared 12-entry corpus (same as the benchmark that caught BGE-small fp) ───

_PATTERNS = [
    # idx 0-3: auth / token domain
    "JWT token expiry should be short-lived, use refresh tokens for persistence",
    "Always validate token signature before trusting claims",
    "Token refresh endpoint must be rate-limited to prevent abuse",
    "Store token secrets in environment variables, never in code",
    # idx 4-7: database domain
    "Run database migrations inside transactions to allow rollback on failure",
    "Index foreign keys before running bulk inserts on large tables",
    "Use connection pooling to avoid exhausting database connections",
    "Never run raw user input in SQL queries without parameterisation",
    # idx 8-11: general engineering
    "Write idempotent background jobs so retries do not cause duplicate side-effects",
    "Avoid catching bare exceptions; catch specific error types",
    "Log structured JSON so downstream tooling can parse fields without regex",
    "Pin dependency versions in requirements files to ensure reproducible builds",
]

_AUTH_IDX = {0, 1, 2, 3}
_DB_IDX = {4, 5, 6, 7}


@pytest.fixture(scope="module")
def corpus_emb():
    from utils.embeddings import encode_document
    emb = encode_document(_PATTERNS)
    assert emb is not None, "encode_document returned None"
    return emb


# ── Regression: specific false positive that broke under BGE-small ─────────────

def test_database_migrations_not_retrieved_for_token_expiry(corpus_emb) -> None:
    """'database migrations' (idx 4) scored 0.618 with BGE-small for 'token expiry'.
    Must stay below SEARCH_THRESHOLD with nomic."""
    from utils.embeddings import encode_query
    q = encode_query(["token expiry"])
    assert q is not None
    sims = cosine_scores(q[0], corpus_emb)
    db_migration_score = float(sims[4])
    assert db_migration_score < SEARCH_THRESHOLD, (
        f"False positive: idx 4 ('database migrations') scored {db_migration_score:.3f} "
        f">= SEARCH_THRESHOLD {SEARCH_THRESHOLD} for 'token expiry' query. "
        f"BGE-small baseline was 0.618."
    )


def test_token_patterns_not_retrieved_for_database_query(corpus_emb) -> None:
    from utils.embeddings import encode_query
    q = encode_query(["database migrations"])
    assert q is not None
    sims = cosine_scores(q[0], corpus_emb)
    for idx in _AUTH_IDX:
        score = float(sims[idx])
        assert score < SEARCH_THRESHOLD, (
            f"False positive: idx {idx} (auth pattern) scored {score:.3f} "
            f">= SEARCH_THRESHOLD for 'database migrations' query"
        )


# ── Precision: zero false positives across three query types ──────────────────

def _precision_for_query(corpus_emb, query: str, relevant: set[int]) -> tuple[int, int]:
    from utils.embeddings import encode_query
    q = encode_query([query])
    assert q is not None
    sims = cosine_scores(q[0], corpus_emb)
    tp = sum(1 for i, s in enumerate(sims) if s >= SEARCH_THRESHOLD and i in relevant)
    fp = sum(1 for i, s in enumerate(sims) if s >= SEARCH_THRESHOLD and i not in relevant)
    return tp, fp


def test_zero_false_positives_token_expiry_query(corpus_emb) -> None:
    _, fp = _precision_for_query(corpus_emb, "token expiry", _AUTH_IDX)
    assert fp == 0, f"Got {fp} false positive(s) for 'token expiry' query"


def test_zero_false_positives_database_query(corpus_emb) -> None:
    _, fp = _precision_for_query(corpus_emb, "database migrations", _DB_IDX)
    assert fp == 0, f"Got {fp} false positive(s) for 'database migrations' query"


def test_zero_false_positives_logging_query(corpus_emb) -> None:
    _, fp = _precision_for_query(corpus_emb, "logging best practices", {10})
    assert fp == 0, f"Got {fp} false positive(s) for 'logging best practices' query"


def test_relevant_patterns_retrieved_for_token_query(corpus_emb) -> None:
    """At minimum the top token pattern (idx 0) must be retrieved."""
    from utils.embeddings import encode_query
    q = encode_query(["token expiry"])
    assert q is not None
    sims = cosine_scores(q[0], corpus_emb)
    assert float(sims[0]) >= SEARCH_THRESHOLD, (
        f"Primary token pattern (idx 0) scored only {float(sims[0]):.3f}; "
        f"expected >= {SEARCH_THRESHOLD}"
    )


def test_relevant_pattern_retrieved_for_database_query(corpus_emb) -> None:
    """The direct migration pattern (idx 4) must always be retrieved."""
    from utils.embeddings import encode_query
    q = encode_query(["database migrations"])
    assert q is not None
    sims = cosine_scores(q[0], corpus_emb)
    assert float(sims[4]) >= SEARCH_THRESHOLD, (
        f"Migration pattern (idx 4) scored only {float(sims[4]):.3f}; "
        f"expected >= {SEARCH_THRESHOLD}"
    )


# ── Dedup threshold ───────────────────────────────────────────────────────────

def test_dedup_catches_near_identical_rephrasing() -> None:
    """Two phrasings of the same rule must exceed DEDUP_THRESHOLD."""
    from utils.embeddings import encode_document
    p1 = "JWT token expiry should be short-lived, use refresh tokens for persistence"
    p2 = "JWT tokens should be short-lived; always use refresh tokens for long sessions"
    embs = encode_document([p1, p2])
    assert embs is not None
    sim = float(cosine_scores(embs[0], embs[1:])[0])
    assert sim > DEDUP_THRESHOLD, (
        f"Near-identical patterns scored {sim:.3f}, expected > {DEDUP_THRESHOLD}. "
        f"Dedup would miss this pair."
    )


def test_dedup_keeps_distinct_patterns() -> None:
    """Patterns from different domains must stay below DEDUP_THRESHOLD."""
    from utils.embeddings import encode_document
    p1 = "JWT token expiry should be short-lived, use refresh tokens for persistence"
    p2 = "Always validate token signature before trusting claims"
    embs = encode_document([p1, p2])
    assert embs is not None
    sim = float(cosine_scores(embs[0], embs[1:])[0])
    assert sim < DEDUP_THRESHOLD, (
        f"Distinct patterns scored {sim:.3f}, expected < {DEDUP_THRESHOLD}. "
        f"Dedup would incorrectly merge them."
    )


def test_dedup_clearly_distinct_domains() -> None:
    """Token pattern vs database pattern must score well below DEDUP_THRESHOLD."""
    from utils.embeddings import encode_document
    p1 = "JWT token expiry should be short-lived, use refresh tokens for persistence"
    p2 = "Run database migrations inside transactions to allow rollback on failure"
    embs = encode_document([p1, p2])
    assert embs is not None
    sim = float(cosine_scores(embs[0], embs[1:])[0])
    assert sim < DEDUP_THRESHOLD - 0.1, (
        f"Cross-domain patterns scored {sim:.3f}; expected well below {DEDUP_THRESHOLD}"
    )


# ── Overall precision gate ────────────────────────────────────────────────────

def test_overall_precision_is_perfect(corpus_emb) -> None:
    """Aggregate precision across all three query types must be 1.0.

    This is the single metric that summarises the model upgrade. BGE-small
    scored ~0.67 on the same corpus. A regression below 1.0 means false
    positives have crept back in.
    """
    queries = [
        ("token expiry", _AUTH_IDX),
        ("database migrations", _DB_IDX),
        ("logging best practices", {10}),
    ]
    total_tp = total_fp = 0
    for q_text, relevant in queries:
        tp, fp = _precision_for_query(corpus_emb, q_text, relevant)
        total_tp += tp
        total_fp += fp

    precision = total_tp / max(total_tp + total_fp, 1)
    assert precision == 1.0, (
        f"Precision = {precision:.2f} (tp={total_tp}, fp={total_fp}). "
        f"Expected 1.0. BGE-small baseline was ~0.67."
    )
