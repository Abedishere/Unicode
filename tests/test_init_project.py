"""Unit tests for utils/init_project.py — project scanning helpers."""
from __future__ import annotations

from pathlib import Path


from utils.init_project import (
    _build_file_tree,
    _has_real_content,
    _needs_memory_upgrade,
    _read_key_files,
    _read_source_samples,
    _upgrade_agent_md,
)


# ── _build_file_tree ──────────────────────────────────────────────────────────

def test_build_file_tree_lists_files(tmp_path: Path) -> None:
    (tmp_path / "main.py").write_text("pass")
    (tmp_path / "utils.py").write_text("pass")
    result = _build_file_tree(str(tmp_path))
    assert "main.py" in result
    assert "utils.py" in result


def test_build_file_tree_skips_node_modules(tmp_path: Path) -> None:
    nm = tmp_path / "node_modules"
    nm.mkdir()
    (nm / "dep.js").write_text("")
    (tmp_path / "app.py").write_text("")
    result = _build_file_tree(str(tmp_path))
    assert "node_modules" not in result
    assert "dep.js" not in result


def test_build_file_tree_skips_pyc(tmp_path: Path) -> None:
    (tmp_path / "main.py").write_text("")
    (tmp_path / "main.pyc").write_text("")
    result = _build_file_tree(str(tmp_path))
    assert "main.pyc" not in result
    assert "main.py" in result


def test_build_file_tree_shows_subdirs(tmp_path: Path) -> None:
    sub = tmp_path / "src"
    sub.mkdir()
    (sub / "app.py").write_text("")
    result = _build_file_tree(str(tmp_path))
    assert "src/" in result
    assert "app.py" in result


def test_build_file_tree_empty_dir(tmp_path: Path) -> None:
    result = _build_file_tree(str(tmp_path))
    assert result == ""


def test_build_file_tree_truncates_at_limit(tmp_path: Path) -> None:
    # Create more files than _MAX_TREE_ENTRIES (100)
    for i in range(110):
        (tmp_path / f"file_{i:03d}.py").write_text("")
    result = _build_file_tree(str(tmp_path))
    assert "truncated" in result


# ── _read_key_files ───────────────────────────────────────────────────────────

def test_read_key_files_reads_readme(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("# My Project\nGreat stuff.")
    result = _read_key_files(str(tmp_path))
    assert "README.md" in result
    assert "My Project" in result


def test_read_key_files_no_files_returns_fallback(tmp_path: Path) -> None:
    result = _read_key_files(str(tmp_path))
    assert "no standard config/meta files found" in result


def test_read_key_files_truncates_large_file(tmp_path: Path) -> None:
    # Write a file larger than _MAX_PER_FILE (1800 chars)
    big = "x" * 3000
    (tmp_path / "README.md").write_text(big)
    result = _read_key_files(str(tmp_path))
    assert "truncated" in result


def test_read_key_files_reads_pyproject(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'foo'")
    result = _read_key_files(str(tmp_path))
    assert "pyproject.toml" in result


def test_read_key_files_missing_file_skipped(tmp_path: Path) -> None:
    # Only README exists; requirements.txt does not
    (tmp_path / "README.md").write_text("hello")
    result = _read_key_files(str(tmp_path))
    assert "requirements.txt" not in result


# ── _read_source_samples ──────────────────────────────────────────────────────

def test_read_source_samples_finds_py_files(tmp_path: Path) -> None:
    (tmp_path / "main.py").write_text("def run(): pass")
    result = _read_source_samples(str(tmp_path))
    assert "main.py" in result


def test_read_source_samples_prioritises_entry_stems(tmp_path: Path) -> None:
    (tmp_path / "main.py").write_text("# entry point")
    (tmp_path / "helper.py").write_text("# helper")
    result = _read_source_samples(str(tmp_path))
    # main.py should appear (entry stem priority)
    assert "main.py" in result


def test_read_source_samples_empty_dir(tmp_path: Path) -> None:
    result = _read_source_samples(str(tmp_path))
    assert "no source files found" in result


def test_read_source_samples_skips_node_modules(tmp_path: Path) -> None:
    nm = tmp_path / "node_modules"
    nm.mkdir()
    (nm / "index.js").write_text("// vendor code")
    (tmp_path / "app.py").write_text("# app")
    result = _read_source_samples(str(tmp_path))
    assert "vendor code" not in result


def test_read_source_samples_truncates_large_file(tmp_path: Path) -> None:
    (tmp_path / "main.py").write_text("x = 1\n" * 2000)
    result = _read_source_samples(str(tmp_path))
    assert "truncated" in result


# ── _has_real_content ─────────────────────────────────────────────────────────

def test_has_real_content_missing_file_returns_false(tmp_path: Path) -> None:
    assert _has_real_content(str(tmp_path), "bugs.md") is False


def test_has_real_content_empty_file_returns_false(tmp_path: Path) -> None:
    orch = tmp_path / ".orchestrator"
    orch.mkdir()
    (orch / "bugs.md").write_text("")
    assert _has_real_content(str(tmp_path), "bugs.md") is False


def test_has_real_content_only_header_returns_false(tmp_path: Path) -> None:
    """The auto-generated header is exactly 4 non-blank lines."""
    orch = tmp_path / ".orchestrator"
    orch.mkdir()
    # Exactly 4 non-blank lines = header only
    header = "# Bugs\n\nMaintained by AI.\nTracks bugs.\n"
    (orch / "bugs.md").write_text(header)
    assert _has_real_content(str(tmp_path), "bugs.md") is False


def test_has_real_content_five_lines_returns_true(tmp_path: Path) -> None:
    orch = tmp_path / ".orchestrator"
    orch.mkdir()
    # Need more than 4 non-blank lines (>4); add two real bug entries
    content = (
        "# Bugs\n\nMaintained by AI.\nTracks bugs.\n"
        "### 2024-01-01 - Bug 1\n- **Issue**: something broke\n"
    )
    (orch / "bugs.md").write_text(content)
    assert _has_real_content(str(tmp_path), "bugs.md") is True


def test_has_real_content_blank_only_file_returns_false(tmp_path: Path) -> None:
    orch = tmp_path / ".orchestrator"
    orch.mkdir()
    (orch / "bugs.md").write_text("\n\n\n\n\n")
    assert _has_real_content(str(tmp_path), "bugs.md") is False


# ── _needs_memory_upgrade ─────────────────────────────────────────────────────

def test_needs_memory_upgrade_missing_file_returns_false(tmp_path: Path) -> None:
    assert _needs_memory_upgrade(str(tmp_path), "CLAUDE.md") is False


def test_needs_memory_upgrade_file_without_protocol_returns_true(tmp_path: Path) -> None:
    (tmp_path / "CLAUDE.md").write_text("# Instructions\nDo stuff.")
    assert _needs_memory_upgrade(str(tmp_path), "CLAUDE.md") is True


def test_needs_memory_upgrade_file_with_protocol_returns_false(tmp_path: Path) -> None:
    (tmp_path / "CLAUDE.md").write_text(
        "# Instructions\nCheck .orchestrator/ for memory.\n"
    )
    assert _needs_memory_upgrade(str(tmp_path), "CLAUDE.md") is False


def test_needs_memory_upgrade_agents_md(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text("Old instructions without the protocol.")
    assert _needs_memory_upgrade(str(tmp_path), "AGENTS.md") is True


# ── _upgrade_agent_md ─────────────────────────────────────────────────────────

def test_upgrade_agent_md_missing_file_returns_false(tmp_path: Path) -> None:
    assert _upgrade_agent_md(str(tmp_path), "CLAUDE.md") is False


def test_upgrade_agent_md_already_upgraded_returns_false(tmp_path: Path) -> None:
    (tmp_path / "CLAUDE.md").write_text("Use .orchestrator/ for memory.")
    assert _upgrade_agent_md(str(tmp_path), "CLAUDE.md") is False


def test_upgrade_agent_md_appends_protocol_returns_true(tmp_path: Path) -> None:
    (tmp_path / "CLAUDE.md").write_text("# Instructions\nDo stuff.")
    result = _upgrade_agent_md(str(tmp_path), "CLAUDE.md")
    assert result is True
    content = (tmp_path / "CLAUDE.md").read_text()
    assert ".orchestrator/" in content
    assert "Do stuff." in content  # original content preserved
