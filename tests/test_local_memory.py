"""Unit tests for utils/local_memory.py."""
from __future__ import annotations

from pathlib import Path

from utils.local_memory import (
    format_local_context,
    increment_local_usage_counts,
    load_local_patterns,
    write_local_patterns,
)


def test_write_and_load_round_trip(tmp_path: Path) -> None:
    entries = [{"category": "error_handling", "pattern": "Validate inputs at boundaries", "context": "Prevents bad data from reaching business logic."}]
    write_local_patterns(str(tmp_path), entries)
    loaded = load_local_patterns(str(tmp_path), "validate input")
    assert len(loaded) >= 1
    assert loaded[0]["pattern"] == "Validate inputs at boundaries"


def test_load_returns_empty_when_no_file(tmp_path: Path) -> None:
    results = load_local_patterns(str(tmp_path), "anything")
    assert results == []


def test_write_assigns_id_and_date(tmp_path: Path) -> None:
    write_local_patterns(str(tmp_path), [{"pattern": "Use context managers for resources", "category": "convention"}])
    loaded = load_local_patterns(str(tmp_path), "context managers")
    assert loaded[0]["id"].startswith("lp_")
    assert loaded[0]["date"]


def test_dedup_by_consecutive_words(tmp_path: Path) -> None:
    entry = {"pattern": "Always close database connections after queries complete", "category": "convention"}
    write_local_patterns(str(tmp_path), [entry])
    write_local_patterns(str(tmp_path), [entry])
    # Second write is a dup — usage_count bumped, not a new entry
    from utils.local_memory import _load_all
    all_entries = _load_all(str(tmp_path))
    assert len(all_entries) == 1
    assert all_entries[0]["usage_count"] == 1


def test_bm25_ranks_relevant_higher(tmp_path: Path) -> None:
    write_local_patterns(str(tmp_path), [
        {"pattern": "Use async handlers for database queries", "category": "performance"},
        {"pattern": "Write unit tests for all public functions", "category": "testing"},
    ])
    results = load_local_patterns(str(tmp_path), "database async query")
    assert results[0]["pattern"] == "Use async handlers for database queries"


def test_format_local_context_header(tmp_path: Path) -> None:
    write_local_patterns(str(tmp_path), [
        {"pattern": "Prefer composition over inheritance", "context": "Easier to test and extend.", "category": "architecture"},
    ])
    pats = load_local_patterns(str(tmp_path), "prefer composition over inheritance in object-oriented design")
    ctx = format_local_context(pats)
    assert "Project-Specific Patterns" in ctx
    assert "Prefer composition over inheritance" in ctx


def test_format_local_context_empty() -> None:
    assert format_local_context([]) == ""


def test_increment_local_usage_counts(tmp_path: Path) -> None:
    write_local_patterns(str(tmp_path), [{"pattern": "Cache expensive computations", "category": "performance"}])
    from utils.local_memory import _load_all
    entries = _load_all(str(tmp_path))
    pat_id = entries[0]["id"]
    increment_local_usage_counts(str(tmp_path), [pat_id])
    updated = _load_all(str(tmp_path))
    assert updated[0]["usage_count"] == 1


def test_quality_score_stored(tmp_path: Path) -> None:
    write_local_patterns(str(tmp_path), [{"pattern": "Log at boundaries not inside logic", "category": "convention", "quality_score": 0.9}])
    from utils.local_memory import _load_all
    entries = _load_all(str(tmp_path))
    assert entries[0]["quality_score"] == 0.9
