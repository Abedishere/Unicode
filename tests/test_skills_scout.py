"""Unit tests for phases/skills_scout.py."""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from phases.skills_scout import (
    SkillsManifest,
    _format_skills_block,
    run_skills_scout,
)


# ── SkillsManifest ────────────────────────────────────────────────────────────

def test_is_empty_all_empty() -> None:
    assert SkillsManifest().is_empty() is True


def test_is_empty_one_populated() -> None:
    m = SkillsManifest(developer="some skill")
    assert m.is_empty() is False


def test_is_empty_all_populated() -> None:
    m = SkillsManifest(
        researcher="r", planner="p", developer="d", reviewer="rev"
    )
    assert m.is_empty() is False


def test_format_for_role_empty_returns_empty_string() -> None:
    m = SkillsManifest()
    assert m.format_for_role("developer") == ""


def test_format_for_role_populated_returns_xml_block() -> None:
    m = SkillsManifest(developer="use pytest for testing")
    result = m.format_for_role("developer")
    assert result.startswith("<skills>")
    assert "use pytest" in result
    assert result.strip().endswith("</skills>")


def test_format_for_role_unknown_role_returns_empty() -> None:
    m = SkillsManifest(developer="skill content")
    assert m.format_for_role("nonexistent_role") == ""


# ── _format_skills_block ──────────────────────────────────────────────────────

def test_format_skills_block_empty_dict() -> None:
    assert _format_skills_block({}) == ""


def test_format_skills_block_single_item() -> None:
    # _format_skills_block uses pkg.split("@")[-1] as the heading,
    # so "my-skill@1.0" → heading is "1.0"
    result = _format_skills_block({"my-skill@1.0": "Use this skill for X."})
    assert "1.0" in result
    assert "Use this skill" in result


def test_format_skills_block_multiple_items_separated() -> None:
    result = _format_skills_block({
        "pkg-a@1.0": "Content A",
        "pkg-b@2.0": "Content B",
    })
    assert "1.0" in result
    assert "2.0" in result
    assert "---" in result  # separator between items


def test_format_skills_block_strips_package_prefix() -> None:
    """Package name without @ → full name used as heading."""
    result = _format_skills_block({"plain-skill": "content"})
    assert "plain-skill" in result


def test_format_skills_block_no_at_sign() -> None:
    result = _format_skills_block({"plain-skill": "plain content"})
    assert "plain-skill" in result


# ── run_skills_scout ──────────────────────────────────────────────────────────

def _stub_qwen(response: str) -> MagicMock:
    q = MagicMock()
    q.query.return_value = response
    return q


def test_run_skills_scout_qwen_exception_returns_empty() -> None:
    qwen = MagicMock()
    qwen.query.side_effect = RuntimeError("Qwen down")
    result = run_skills_scout("build a todo app", qwen)
    assert result.is_empty()


def test_run_skills_scout_invalid_json_returns_empty() -> None:
    qwen = _stub_qwen("this is not json at all")
    result = run_skills_scout("build a todo app", qwen)
    assert result.is_empty()


def test_run_skills_scout_empty_json_returns_empty() -> None:
    qwen = _stub_qwen("{}")
    result = run_skills_scout("build a todo app", qwen)
    assert result.is_empty()


def test_run_skills_scout_valid_json_no_results() -> None:
    """Valid JSON but search_skills returns no packages — manifest stays empty."""
    queries = {
        "researcher": ["q1", "q2"],
        "planner": ["q3", "q4"],
        "developer": ["q5", "q6"],
        "reviewer": ["q7", "q8"],
    }
    qwen = _stub_qwen(json.dumps(queries))
    with patch("phases.skills_scout.search_skills", return_value=[]):
        result = run_skills_scout("task", qwen)
    assert result.is_empty()


def test_run_skills_scout_installs_and_populates_manifest() -> None:
    """Happy path: search finds packages, install returns content."""
    queries = {
        "researcher": ["find tools"],
        "planner": ["architecture tools"],
        "developer": ["python testing"],
        "reviewer": ["linting tools"],
    }
    qwen = _stub_qwen(json.dumps(queries))

    with (
        patch("phases.skills_scout.search_skills", return_value=["cool-skill@1.0"]),
        patch(
            "phases.skills_scout.discover_and_install",
            return_value={"cool-skill@1.0": "# Cool Skill\nDo stuff."},
        ),
    ):
        result = run_skills_scout("build todo", qwen)

    # At least one role should have content
    assert not result.is_empty()


def test_run_skills_scout_deduplicates_packages() -> None:
    """The same package is not assigned to two different roles."""
    queries = {
        "researcher": ["q1"],
        "planner": ["q2"],
        "developer": ["q3"],
        "reviewer": ["q4"],
    }
    qwen = _stub_qwen(json.dumps(queries))

    assigned_packages: list[list] = []

    def fake_install(pkgs: list[str]) -> dict:
        assigned_packages.append(list(pkgs))
        return {p: "content" for p in pkgs}

    with (
        patch("phases.skills_scout.search_skills", return_value=["shared-pkg@1.0"]),
        patch("phases.skills_scout.discover_and_install", side_effect=fake_install),
    ):
        run_skills_scout("task", qwen)

    # shared-pkg should appear in at most one role's install call
    all_pkgs = [p for batch in assigned_packages for p in batch]
    assert all_pkgs.count("shared-pkg@1.0") <= 1
