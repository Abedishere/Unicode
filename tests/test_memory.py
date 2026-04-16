"""Unit tests for utils/memory.py."""
from __future__ import annotations

from pathlib import Path


from utils.memory import (
    _default_memory,
    add_learning,
    add_task_to_index,
    extract_keywords_from_task,
    load_memory,
    parse_json_response,
    save_memory,
    search_past_tasks,
)


# ── parse_json_response ───────────────────────────────────────────────────────

def test_parse_json_plain() -> None:
    assert parse_json_response('{"key": "value"}') == {"key": "value"}


def test_parse_json_fenced() -> None:
    raw = '```json\n{"foo": 1}\n```'
    assert parse_json_response(raw) == {"foo": 1}


def test_parse_json_embedded() -> None:
    raw = 'Some text before {"answer": 42} and after'
    assert parse_json_response(raw) == {"answer": 42}


def test_parse_json_empty_string() -> None:
    assert parse_json_response("") == {}


def test_parse_json_no_json() -> None:
    assert parse_json_response("plain text with no braces") == {}


def test_parse_json_malformed() -> None:
    assert parse_json_response("{not valid json}") == {}


# ── load_memory / save_memory round-trip ─────────────────────────────────────

def test_load_memory_missing_file(tmp_path: Path) -> None:
    """Missing memory.yaml should return the default structure without crashing."""
    mem = load_memory(str(tmp_path))
    assert isinstance(mem, dict)
    for key in _default_memory():
        assert key in mem


def test_save_and_load_round_trip(tmp_path: Path) -> None:
    mem = _default_memory()
    mem["patterns_learned"].append({"date": "2026-01-01", "text": "test pattern"})
    save_memory(str(tmp_path), mem)

    loaded = load_memory(str(tmp_path))
    assert loaded["patterns_learned"][0]["text"] == "test pattern"


def test_load_memory_malformed_yaml(tmp_path: Path) -> None:
    mem_path = tmp_path / ".orchestrator" / "memory.yaml"
    mem_path.parent.mkdir(parents=True)
    mem_path.write_text(": invalid: yaml: {{", encoding="utf-8")

    mem = load_memory(str(tmp_path))
    assert isinstance(mem, dict)  # falls back to defaults, does not raise


def test_save_memory_prunes_old_entries(tmp_path: Path) -> None:
    """Lists exceeding 20 entries should be pruned to the 20 most recent."""
    mem = _default_memory()
    for i in range(25):
        mem["patterns_learned"].append({"date": "2026-01-01", "text": f"entry {i}"})
    save_memory(str(tmp_path), mem)

    loaded = load_memory(str(tmp_path))
    assert len(loaded["patterns_learned"]) == 20
    # Most recent entries are kept
    assert loaded["patterns_learned"][-1]["text"] == "entry 24"


# ── add_task_to_index ─────────────────────────────────────────────────────────

def test_add_task_to_index(tmp_path: Path) -> None:
    add_task_to_index(str(tmp_path), "build auth module", "completed", ["auth", "jwt"])
    mem = load_memory(str(tmp_path))
    assert len(mem["task_index"]) == 1
    assert mem["task_index"][0]["task"] == "build auth module"
    assert "jwt" in mem["task_index"][0]["keywords"]


def test_add_multiple_tasks(tmp_path: Path) -> None:
    add_task_to_index(str(tmp_path), "task A", "done", [])
    add_task_to_index(str(tmp_path), "task B", "done", [])
    mem = load_memory(str(tmp_path))
    assert len(mem["task_index"]) == 2


# ── search_past_tasks ─────────────────────────────────────────────────────────

def test_search_finds_matching_task(tmp_path: Path) -> None:
    add_task_to_index(str(tmp_path), "implement authentication", "done", ["auth", "login"])
    results = search_past_tasks(str(tmp_path), "auth module")
    assert len(results) >= 1
    assert "authentication" in results[0]["task"]


def test_search_returns_empty_for_no_match(tmp_path: Path) -> None:
    add_task_to_index(str(tmp_path), "fix database migration", "done", ["db", "sql"])
    results = search_past_tasks(str(tmp_path), "authentication jwt")
    assert results == []


# ── add_learning ──────────────────────────────────────────────────────────────

def test_add_learning_appends_entry(tmp_path: Path) -> None:
    add_learning(str(tmp_path), "codebase_conventions", "Always use type hints")
    mem = load_memory(str(tmp_path))
    assert any("type hints" in e.get("text", "") for e in mem["codebase_conventions"])


# ── extract_keywords_from_task ────────────────────────────────────────────────

def test_extract_keywords_returns_list() -> None:
    kws = extract_keywords_from_task("implement JWT authentication for user login")
    assert isinstance(kws, list)
    assert len(kws) > 0


def test_extract_keywords_filters_stopwords() -> None:
    kws = extract_keywords_from_task("add the new feature to the codebase")
    assert "the" not in kws
    assert "add" not in kws


def test_extract_keywords_max_15() -> None:
    task = " ".join(f"keyword{i}" for i in range(30))
    kws = extract_keywords_from_task(task)
    assert len(kws) <= 15
