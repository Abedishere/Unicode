"""Local semantic embeddings via fastembed (ONNX, no PyTorch required).

Model: BAAI/bge-small-en-v1.5 (~60 MB, cached to ~/.cache/fastembed/ on first use)
Dimensions: 384

Callers should check is_available() and fall back to BM25 when False,
or when encode() returns None.
"""
from __future__ import annotations

import functools
from pathlib import Path

import numpy as np

MODEL_NAME = "nomic-ai/nomic-embed-text-v1.5"
_DIM = 768
DEDUP_THRESHOLD = 0.82   # cosine similarity (pattern-only) above which two entries are duplicates
SEARCH_THRESHOLD = 0.60  # minimum cosine similarity to include a pattern in search results
SEMANTIC_FLOOR = 0.40    # absolute cosine floor — entries below this are excluded even if BM25 boosts them


@functools.lru_cache(maxsize=1)
def _get_model():
    from fastembed import TextEmbedding  # type: ignore[import]
    return TextEmbedding(model_name=MODEL_NAME)


def is_available() -> bool:
    try:
        import fastembed  # noqa: F401
        return True
    except ImportError:
        return False


def _encode_raw(texts: list[str]) -> np.ndarray | None:
    """Return (N, 768) normalized float32 matrix, or None on failure."""
    if not texts:
        return np.zeros((0, _DIM), dtype=np.float32)
    try:
        model = _get_model()
        arr = np.array(list(model.embed(texts)), dtype=np.float32)
        norms = np.linalg.norm(arr, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        return arr / norms
    except Exception:
        return None


def encode_query(texts: list[str]) -> np.ndarray | None:
    """Encode search queries with the required task prefix."""
    return _encode_raw([f"search_query: {t}" for t in texts])


def encode_document(texts: list[str]) -> np.ndarray | None:
    """Encode corpus documents with the required task prefix."""
    return _encode_raw([f"search_document: {t}" for t in texts])


# Alias so old call sites degrade gracefully rather than crash
encode = encode_document


def cosine_scores(query_emb: np.ndarray, corpus_emb: np.ndarray) -> np.ndarray:
    """1-D cosine similarities (dot product of normalized vectors)."""
    if corpus_emb.shape[0] == 0:
        return np.array([], dtype=np.float32)
    return (corpus_emb @ query_emb).astype(np.float32)


def load_emb(path: Path, n: int) -> np.ndarray | None:
    """Load .npy matrix; return None if absent or shape != (n, _DIM)."""
    if not path.exists():
        return None
    try:
        arr = np.load(str(path)).astype(np.float32)
        return arr if arr.shape == (n, _DIM) else None
    except Exception:
        return None


def save_emb(path: Path, arr: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    np.save(str(path), arr)


def mmr_rerank(
    query_emb: np.ndarray,
    corpus_emb: np.ndarray,
    relevance_scores: np.ndarray,
    n: int = 8,
    lambda_: float = 0.7,
) -> list[int]:
    """Maximum Marginal Relevance selection for diverse results.

    Iteratively picks the next candidate that maximises:
      lambda * relevance - (1 - lambda) * max_similarity_to_already_selected

    Returns indices into corpus_emb.
    lambda_=1.0 is pure relevance; 0.0 is pure diversity.
    """
    total = corpus_emb.shape[0]
    if total == 0:
        return []

    candidates = list(range(total))
    selected: list[int] = []

    for _ in range(min(n, total)):
        if not candidates:
            break
        if not selected:
            best = max(candidates, key=lambda i: relevance_scores[i])
        else:
            sel_emb = corpus_emb[selected]
            best, best_score = candidates[0], -float("inf")
            for i in candidates:
                rel = float(relevance_scores[i])
                max_sim = float(np.max(sel_emb @ corpus_emb[i]))
                score = lambda_ * rel - (1 - lambda_) * max_sim
                if score > best_score:
                    best_score, best = score, i
        selected.append(best)
        candidates.remove(best)

    return selected
