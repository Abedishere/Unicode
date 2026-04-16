"""Unit tests for utils/skills_discovery.py."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from utils.skills_discovery import (
    discover_and_install,
    install_skill,
    read_skill_content,
    search_skills,
)


# ── search_skills ─────────────────────────────────────────────────────────────

def _make_proc(stdout: str = "", stderr: str = "", returncode: int = 0) -> MagicMock:
    m = MagicMock()
    m.stdout = stdout
    m.stderr = stderr
    m.returncode = returncode
    return m


def test_search_skills_returns_packages() -> None:
    output = "owner/repo@my-skill  120 installs\nowner/repo@other-skill  50 installs\n"
    with patch("utils.skills_discovery.subprocess.run", return_value=_make_proc(stdout=output)):
        result = search_skills("testing tools")
    assert "owner/repo@my-skill" in result
    assert "owner/repo@other-skill" in result


def test_search_skills_strips_ansi() -> None:
    # ANSI codes around the package name
    output = "\x1b[32mowner/repo@colored-skill\x1b[0m  10 installs\n"
    with patch("utils.skills_discovery.subprocess.run", return_value=_make_proc(stdout=output)):
        result = search_skills("color")
    assert "owner/repo@colored-skill" in result


def test_search_skills_deduplicates() -> None:
    output = "owner/repo@dupe  5 installs\nowner/repo@dupe  5 installs\n"
    with patch("utils.skills_discovery.subprocess.run", return_value=_make_proc(stdout=output)):
        result = search_skills("dupe")
    assert result.count("owner/repo@dupe") == 1


def test_search_skills_respects_top_n() -> None:
    lines = "\n".join(f"owner/repo@skill-{i}  {i} installs" for i in range(10))
    with patch("utils.skills_discovery.subprocess.run", return_value=_make_proc(stdout=lines)):
        result = search_skills("tools", top_n=2)
    assert len(result) == 2


def test_search_skills_returns_empty_on_exception() -> None:
    with patch("utils.skills_discovery.subprocess.run", side_effect=RuntimeError("npx not found")):
        result = search_skills("anything")
    assert result == []


def test_search_skills_excludes_placeholder() -> None:
    """The literal 'owner/repo@skill' placeholder should not appear in results."""
    output = "owner/repo@skill  — install with: npx skills add owner/repo@skill\n"
    with patch("utils.skills_discovery.subprocess.run", return_value=_make_proc(stdout=output)):
        result = search_skills("placeholder")
    assert "owner/repo@skill" not in result


def test_search_skills_uses_stderr_too() -> None:
    """Some NPX versions write package list to stderr."""
    with patch(
        "utils.skills_discovery.subprocess.run",
        return_value=_make_proc(stderr="owner/repo@from-stderr  8 installs"),
    ):
        result = search_skills("tools")
    assert "owner/repo@from-stderr" in result


# ── install_skill ─────────────────────────────────────────────────────────────

def test_install_skill_success_returns_true() -> None:
    with patch("utils.skills_discovery.subprocess.run", return_value=_make_proc(returncode=0)):
        assert install_skill("owner/repo@skill") is True


def test_install_skill_nonzero_returns_false() -> None:
    with patch("utils.skills_discovery.subprocess.run", return_value=_make_proc(returncode=1)):
        assert install_skill("owner/repo@skill") is False


def test_install_skill_exception_returns_false() -> None:
    with patch("utils.skills_discovery.subprocess.run", side_effect=FileNotFoundError("npx")):
        assert install_skill("owner/repo@skill") is False


# ── read_skill_content ────────────────────────────────────────────────────────

def test_read_skill_content_found(tmp_path: Path) -> None:
    skill_dir = tmp_path / "my-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("# My Skill\nUse me.")
    with patch("utils.skills_discovery._GLOBAL_SKILLS_DIRS", [tmp_path]):
        result = read_skill_content("owner/repo@my-skill")
    assert "My Skill" in result


def test_read_skill_content_not_found_returns_empty(tmp_path: Path) -> None:
    with patch("utils.skills_discovery._GLOBAL_SKILLS_DIRS", [tmp_path]):
        result = read_skill_content("owner/repo@nonexistent")
    assert result == ""


def test_read_skill_content_extracts_name_after_at(tmp_path: Path) -> None:
    """Package 'ns/pkg@skill-name' → looks for 'skill-name/SKILL.md'."""
    skill_dir = tmp_path / "skill-name"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("content")
    with patch("utils.skills_discovery._GLOBAL_SKILLS_DIRS", [tmp_path]):
        result = read_skill_content("ns/pkg@skill-name")
    assert result == "content"


def test_read_skill_content_no_at_sign_uses_full_name(tmp_path: Path) -> None:
    """Package without '@' uses the full string as the skill name."""
    skill_dir = tmp_path / "plain-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("plain content")
    with patch("utils.skills_discovery._GLOBAL_SKILLS_DIRS", [tmp_path]):
        result = read_skill_content("plain-skill")
    assert result == "plain content"


def test_read_skill_content_caps_at_limit(tmp_path: Path) -> None:
    skill_dir = tmp_path / "big-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("x" * 5000)
    with patch("utils.skills_discovery._GLOBAL_SKILLS_DIRS", [tmp_path]):
        result = read_skill_content("owner/repo@big-skill")
    assert len(result) <= 2000  # _SKILL_CONTENT_CAP


# ── discover_and_install ──────────────────────────────────────────────────────

def test_discover_and_install_already_installed() -> None:
    """If read_skill_content finds content, install_skill should NOT be called."""
    with (
        patch("utils.skills_discovery.read_skill_content", return_value="content"),
        patch("utils.skills_discovery.install_skill") as mock_install,
    ):
        result = discover_and_install(["pkg@1.0"])
    mock_install.assert_not_called()
    assert result == {"pkg@1.0": "content"}


def test_discover_and_install_installs_missing() -> None:
    """If not installed, installs and then reads content."""
    with (
        patch(
            "utils.skills_discovery.read_skill_content",
            side_effect=["", "new content"],
        ),
        patch("utils.skills_discovery.install_skill", return_value=True),
    ):
        result = discover_and_install(["pkg@1.0"])
    assert result == {"pkg@1.0": "new content"}


def test_discover_and_install_install_fails_excluded() -> None:
    """If install succeeds but SKILL.md still not found, pkg excluded."""
    with (
        patch("utils.skills_discovery.read_skill_content", return_value=""),
        patch("utils.skills_discovery.install_skill", return_value=True),
    ):
        result = discover_and_install(["pkg@1.0"])
    assert result == {}


def test_discover_and_install_multiple_packages(tmp_path: Path) -> None:
    contents = {"a@1.0": "content-a", "b@1.0": "content-b"}
    with (
        patch(
            "utils.skills_discovery.read_skill_content",
            side_effect=lambda p: contents.get(p, ""),
        ),
        patch("utils.skills_discovery.install_skill"),
    ):
        result = discover_and_install(["a@1.0", "b@1.0"])
    assert result == {"a@1.0": "content-a", "b@1.0": "content-b"}
