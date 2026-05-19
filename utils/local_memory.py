"""Per-project local memory store.

Patterns learned within a project are written to .orchestrator/local_patterns.yaml
and surfaced at the start of every subsequent run on that project. Unlike global
memory, local patterns may contain project-specific details (file paths, local
conventions, specific bugs).

Three files per project:
  local_patterns.yaml     machine-written index
  local_patterns_emb.npy  embedding matrix for semantic search (row i = entry i)
  local_wisdom.md         human-readable append-only log
"""
from __future__ import annotations

import hashlib
from datetime import datetime
from pathlib import Path

import yaml

from utils.memory import _tokenize


# ── Paths ─────────────────────────────────────────────────────────────────────

def _yaml_path(work_dir: str) -> Path:
    return Path(work_dir) / ".orchestrator" / "local_patterns.yaml"


def _emb_path(work_dir: str) -> Path:
    return Path(work_dir) / ".orchestrator" / "local_patterns_emb.npy"


def _md_path(work_dir: str) -> Path:
    return Path(work_dir) / ".orchestrator" / "local_wisdom.md"


# ── Helpers ───────────────────────────────────────────────────────────────────

def _entry_text(e: dict) -> str:
    return e.get("pattern", "") + " " + e.get("context", "")


def _share_n_consecutive_words(a: str, b: str, n: int = 5) -> bool:
    words_a = a.lower().split()
    words_b = b.lower().split()
    if len(words_a) < n or len(words_b) < n:
        return False
    ngrams_a = set(" ".join(words_a[i:i + n]) for i in range(len(words_a) - n + 1))
    ngrams_b = set(" ".join(words_b[i:i + n]) for i in range(len(words_b) - n + 1))
    return bool(ngrams_a & ngrams_b)


def _make_id() -> str:
    ts = datetime.now().strftime("%Y%m%d%H%M%S")
    h = hashlib.md5(ts.encode()).hexdigest()[:4]
    return f"lp_{ts}_{h}"


# ── Read ──────────────────────────────────────────────────────────────────────

def _load_all(work_dir: str) -> list[dict]:
    p = _yaml_path(work_dir)
    if not p.exists():
        return []
    try:
        return yaml.safe_load(p.read_text(encoding="utf-8")) or []
    except Exception:
        return []


def load_local_patterns(work_dir: str, task: str, n: int = 8) -> list[dict]:
    """Return up to *n* diverse, relevant local patterns for *task*.

    Pipeline: semantic cosine filter (SEARCH_THRESHOLD) → quality weighting → MMR rerank.
    Falls back to BM25Plus if fastembed is unavailable.
    """
    entries = _load_all(work_dir)
    if not entries:
        return []

    # ── Semantic + MMR path ───────────────────────────────────────────────────
    try:
        from utils.embeddings import (
            is_available, encode_query, encode_document, cosine_scores, load_emb, save_emb,
            SEARCH_THRESHOLD, mmr_rerank,
        )
        import numpy as np
        if is_available():
            q_emb = encode_query([task])
            if q_emb is not None:
                ep = _emb_path(work_dir)
                corpus_emb = load_emb(ep, len(entries))
                if corpus_emb is None:
                    corpus_emb = encode_document([_entry_text(e) for e in entries])
                    if corpus_emb is not None:
                        save_emb(ep, corpus_emb)
                if corpus_emb is not None:
                    sims = cosine_scores(q_emb[0], corpus_emb)
                    keep = np.where(np.asarray(sims) >= SEARCH_THRESHOLD)[0]
                    if keep.size:
                        rel = np.array([
                            float(sims[i]) * (0.4 + 0.6 * entries[i].get("quality_score", 0.5))
                            for i in keep
                        ])
                        sel = mmr_rerank(q_emb[0], corpus_emb[keep], rel, n=n)
                        return [entries[keep[i]] for i in sel]
                    return []
    except Exception:
        pass

    # ── BM25 fallback ─────────────────────────────────────────────────────────
    try:
        from rank_bm25 import BM25Plus
        corpus = [_tokenize(_entry_text(e)) for e in entries]
        if not any(corpus):
            return entries[:n]
        bm25 = BM25Plus(corpus)
        query_tokens = _tokenize(task)
        if not query_tokens:
            return entries[:n]
        raw_scores = bm25.get_scores(query_tokens)
        ranked = sorted(
            [(raw * (0.4 + 0.6 * e.get("quality_score", 0.5)), e)
             for e, raw in zip(entries, raw_scores) if raw > 0],
            key=lambda x: x[0], reverse=True,
        )
        return [e for _, e in ranked[:n]]
    except Exception:
        return entries[:n]


def format_local_context(patterns: list[dict]) -> str:
    """Format a list of local patterns into a prompt-ready string."""
    if not patterns:
        return ""

    by_cat: dict[str, list[dict]] = {}
    for p in patterns:
        cat = p.get("category", "general")
        by_cat.setdefault(cat, []).append(p)

    lines = ["## Project-Specific Patterns (learned from previous runs on this project)"]
    char_budget = 800
    used = len(lines[0])

    for cat, entries in sorted(by_cat.items()):
        for e in entries:
            pattern = e.get("pattern", "")
            context = e.get("context", "")
            line = f"[{cat}] {pattern}"
            detail = f"  ->{context}" if context else ""
            chunk = line + ("\n" + detail if detail else "")
            if used + len(chunk) > char_budget:
                remaining = char_budget - used - len(line) - 6
                if remaining > 20 and detail:
                    chunk = line + f"\n  ->{context[:remaining]}..."
                else:
                    chunk = line
            lines.append(chunk)
            used += len(chunk)
            if used >= char_budget:
                break
        if used >= char_budget:
            break

    return "\n".join(lines)


# ── Write ─────────────────────────────────────────────────────────────────────

def write_local_patterns(work_dir: str, new_entries: list[dict]) -> None:
    """Append new entries to the local store, deduplicating semantically."""
    if not new_entries:
        return

    existing = _load_all(work_dir)
    existing_patterns = [e.get("pattern", "") for e in existing]

    use_semantic = False
    corpus_emb = None    # pattern+context embeddings for search
    dedup_emb = None     # pattern-only embeddings for dedup

    try:
        from utils.embeddings import is_available, encode_document, cosine_scores, load_emb, DEDUP_THRESHOLD
        import numpy as np
        if is_available():
            use_semantic = True
            if existing:
                corpus_emb = load_emb(_emb_path(work_dir), len(existing))
                if corpus_emb is None:
                    corpus_emb = encode_document([_entry_text(e) for e in existing])
                # Pattern-only embeddings for dedup (context dilutes similarity)
                dedup_emb = encode_document([e.get("pattern", "") for e in existing])
    except Exception:
        use_semantic = False

    added = []

    for entry in new_entries:
        pattern_text = entry.get("pattern", "").strip()
        if not pattern_text:
            continue

        is_dup = False

        if use_semantic:
            try:
                new_pattern_emb = encode_document([pattern_text])
                if new_pattern_emb is not None:
                    if dedup_emb is not None and dedup_emb.shape[0] > 0:
                        sims = cosine_scores(new_pattern_emb[0], dedup_emb)
                        max_idx = int(np.argmax(sims))
                        if float(sims[max_idx]) > DEDUP_THRESHOLD:
                            existing[max_idx]["usage_count"] = existing[max_idx].get("usage_count", 0) + 1
                            is_dup = True
                    if not is_dup:
                        dedup_emb = (
                            np.vstack([dedup_emb, new_pattern_emb])
                            if dedup_emb is not None and dedup_emb.shape[0] > 0
                            else new_pattern_emb
                        )
                        new_search_emb = encode_document([_entry_text(entry)])
                        if new_search_emb is not None:
                            corpus_emb = (
                                np.vstack([corpus_emb, new_search_emb])
                                if corpus_emb is not None and corpus_emb.shape[0] > 0
                                else new_search_emb
                            )
            except Exception:
                use_semantic = False

        if not is_dup and not use_semantic:
            dup_idx = next(
                (i for i, ep in enumerate(existing_patterns)
                 if _share_n_consecutive_words(pattern_text, ep)),
                None,
            )
            if dup_idx is not None:
                existing[dup_idx]["usage_count"] = existing[dup_idx].get("usage_count", 0) + 1
                is_dup = True

        if not is_dup:
            new_entry = {
                "id": _make_id(),
                "date": datetime.now().strftime("%Y-%m-%d"),
                "category": entry.get("category", "general"),
                "pattern": pattern_text,
                "context": entry.get("context", ""),
                "quality_score": round(float(entry.get("quality_score", 0.5)), 3),
                "usage_count": 0,
            }
            existing.append(new_entry)
            existing_patterns.append(pattern_text)
            added.append(new_entry)

    p = _yaml_path(work_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        yaml.dump(existing, f, default_flow_style=False, allow_unicode=True, sort_keys=False)

    if use_semantic and corpus_emb is not None:
        try:
            from utils.embeddings import save_emb
            save_emb(_emb_path(work_dir), corpus_emb)
        except Exception:
            pass

    if added:
        _append_wisdom_md(work_dir, added)


def _append_wisdom_md(work_dir: str, entries: list[dict]) -> None:
    p = _md_path(work_dir)
    if not p.exists():
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("# Local Wisdom\n\nProject-specific patterns learned by the AI Orchestrator.\n\n", encoding="utf-8")
    lines = []
    for e in entries:
        cat = e.get("category", "general")
        pattern = e.get("pattern", "")
        context = e.get("context", "")
        date = e.get("date", "")
        lines.append(f"\n### {cat.title()}")
        lines.append(f"- [{date}] {pattern}")
        if context:
            lines.append(f"  - {context}")
    with open(p, "a", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


# ── Usage tracking ────────────────────────────────────────────────────────────

def increment_local_usage_counts(work_dir: str, ids: list[str]) -> None:
    if not ids:
        return
    entries = _load_all(work_dir)
    if not entries:
        return
    id_set = set(ids)
    modified = False
    for e in entries:
        if e.get("id") in id_set:
            e["usage_count"] = e.get("usage_count", 0) + 1
            modified = True
    if modified:
        p = _yaml_path(work_dir)
        with open(p, "w", encoding="utf-8") as f:
            yaml.dump(entries, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
