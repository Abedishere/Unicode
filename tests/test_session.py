"""Unit tests for utils/session.py."""
from __future__ import annotations

from pathlib import Path

import pytest

from utils.session import (
    PHASE_ORDER,
    Session,
    _sessions_dir,
    list_sessions,
    load_session,
    save_session,
)


# ── Session construction ──────────────────────────────────────────────────────

def test_session_defaults() -> None:
    s = Session(task="do stuff")
    assert s.task == "do stuff"
    assert s.tier == "standard"
    assert s.status == "created"
    assert len(s.session_id) == 8  # uuid4().hex[:8]
    assert all(s.phases[p] is None for p in PHASE_ORDER)


def test_session_custom_id() -> None:
    s = Session(session_id="abc12345", task="t")
    assert s.session_id == "abc12345"


# ── Session helpers ───────────────────────────────────────────────────────────

def test_mark_phase_done() -> None:
    s = Session()
    s.mark_phase_done("plan", "plan text")
    assert s.phases["plan"] == "plan text"


def test_phase_done_true() -> None:
    s = Session()
    s.mark_phase_done("plan", "result")
    assert s.phase_done("plan") is True


def test_phase_done_false() -> None:
    s = Session()
    assert s.phase_done("plan") is False


def test_next_incomplete_phase_first() -> None:
    s = Session()
    assert s.next_incomplete_phase() == PHASE_ORDER[0]


def test_next_incomplete_phase_after_first_done() -> None:
    s = Session()
    s.mark_phase_done(PHASE_ORDER[0], "done")
    assert s.next_incomplete_phase() == PHASE_ORDER[1]


def test_next_incomplete_phase_all_done() -> None:
    s = Session()
    for p in PHASE_ORDER:
        s.mark_phase_done(p, "done")
    assert s.next_incomplete_phase() is None


# ── Serialization round-trip ──────────────────────────────────────────────────

def test_to_dict_from_dict_round_trip() -> None:
    s = Session(task="build auth", tier="complex", cfg={"x": 1})
    s.status = "running"
    s.mark_phase_done("plan", "plan text")
    s.current_phase = "implement"

    d = s.to_dict()
    s2 = Session.from_dict(d)

    assert s2.task == "build auth"
    assert s2.tier == "complex"
    assert s2.cfg == {"x": 1}
    assert s2.status == "running"
    assert s2.phases["plan"] == "plan text"
    assert s2.current_phase == "implement"


def test_from_dict_missing_fields() -> None:
    """from_dict handles dicts with missing optional fields gracefully."""
    s = Session.from_dict({"session_id": "aaaabbbb"})
    assert s.session_id == "aaaabbbb"
    assert s.task == ""
    assert s.tier == "standard"
    assert s.status == "unknown"


def test_from_dict_preserves_phases() -> None:
    d = {
        "session_id": "x1",
        "phases": {"plan": "done", "discussion": None, "implement": None,
                   "review": None, "finalize": None},
    }
    s = Session.from_dict(d)
    assert s.phases["plan"] == "done"
    assert s.phases["discussion"] is None


# ── File I/O ──────────────────────────────────────────────────────────────────

def test_sessions_dir_creates_path(tmp_path: Path) -> None:
    p = _sessions_dir(str(tmp_path))
    assert p.is_dir()


def test_save_and_load_round_trip(tmp_path: Path) -> None:
    s = Session(task="unit test", tier="quick")
    s.mark_phase_done("plan", "the plan")
    save_session(str(tmp_path), s)

    loaded = load_session(str(tmp_path), s.session_id)
    assert loaded is not None
    assert loaded.task == "unit test"
    assert loaded.phases["plan"] == "the plan"


def test_load_session_missing_returns_none(tmp_path: Path) -> None:
    result = load_session(str(tmp_path), "doesnotexist")
    assert result is None


def test_load_session_corrupted_returns_none(tmp_path: Path) -> None:
    sdir = _sessions_dir(str(tmp_path))
    (sdir / "badfile.json").write_text("not json {{", encoding="utf-8")
    result = load_session(str(tmp_path), "badfile")
    assert result is None


def test_list_sessions_empty(tmp_path: Path) -> None:
    sessions = list_sessions(str(tmp_path))
    assert sessions == []


def test_list_sessions_returns_all(tmp_path: Path) -> None:
    for task in ("task A", "task B", "task C"):
        save_session(str(tmp_path), Session(task=task))
    sessions = list_sessions(str(tmp_path))
    assert len(sessions) == 3


def test_list_sessions_skips_corrupt(tmp_path: Path) -> None:
    save_session(str(tmp_path), Session(task="good"))
    sdir = _sessions_dir(str(tmp_path))
    (sdir / "corrupt.json").write_text("{{bad", encoding="utf-8")
    sessions = list_sessions(str(tmp_path))
    assert len(sessions) == 1
    assert sessions[0].task == "good"
