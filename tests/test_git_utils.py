"""Unit tests for utils/git_utils.py."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

import utils.git_utils as git_utils
from utils.git_utils import (
    _only_warnings,
    configure_workspace_git,
    is_git_repo,
    run_git,
)


@pytest.fixture(autouse=True)
def reset_configured_dirs():
    """Clear the _configured_dirs cache before every test."""
    git_utils._configured_dirs.clear()
    yield
    git_utils._configured_dirs.clear()


def _proc(stdout: str = "", stderr: str = "", returncode: int = 0) -> MagicMock:
    m = MagicMock()
    m.stdout = stdout
    m.stderr = stderr
    m.returncode = returncode
    return m


# ── _only_warnings ────────────────────────────────────────────────────────────

def test_only_warnings_empty_stderr() -> None:
    assert _only_warnings("") is True


def test_only_warnings_blank_lines_only() -> None:
    assert _only_warnings("\n\n  \n") is True


def test_only_warnings_single_warning() -> None:
    assert _only_warnings("warning: LF will be replaced by CRLF") is True


def test_only_warnings_multiple_warnings() -> None:
    stderr = "warning: foo\nwarning: bar\n"
    assert _only_warnings(stderr) is True


def test_only_warnings_error_line_returns_false() -> None:
    stderr = "warning: foo\nerror: bad object 'HEAD'"
    assert _only_warnings(stderr) is False


def test_only_warnings_non_warning_first_line() -> None:
    assert _only_warnings("fatal: not a git repository") is False


def test_only_warnings_mixed_blank_and_warning() -> None:
    stderr = "\nwarning: sparse checkout leaves no entry on working directory\n"
    assert _only_warnings(stderr) is True


# ── run_git ───────────────────────────────────────────────────────────────────

def test_run_git_success_returns_stdout() -> None:
    with patch("utils.git_utils.subprocess.run", return_value=_proc(stdout="  abc123  ")):
        result = run_git(["rev-parse", "HEAD"], "/some/dir")
    assert result == "abc123"


def test_run_git_nonzero_only_warnings_returns_stdout() -> None:
    with patch(
        "utils.git_utils.subprocess.run",
        return_value=_proc(stdout="staged", stderr="warning: LF will be replaced", returncode=1),
    ):
        result = run_git(["add", "-A"], "/some/dir")
    assert result == "staged"


def test_run_git_nonzero_real_error_raises() -> None:
    with patch(
        "utils.git_utils.subprocess.run",
        return_value=_proc(stderr="fatal: not a repo", returncode=128),
    ):
        with pytest.raises(RuntimeError, match="not a repo"):
            run_git(["status"], "/bad/dir")


def test_run_git_passes_correct_args() -> None:
    mock_run = MagicMock(return_value=_proc(stdout="ok"))
    with patch("utils.git_utils.subprocess.run", mock_run):
        run_git(["log", "--oneline"], "/repo")
    called_args = mock_run.call_args[0][0]
    assert called_args == ["git", "log", "--oneline"]


# ── configure_workspace_git ───────────────────────────────────────────────────

def test_configure_workspace_git_calls_subprocess(tmp_path) -> None:
    with patch("utils.git_utils.subprocess.run") as mock_run:
        configure_workspace_git(str(tmp_path))
    # Should call subprocess 3 times (autocrlf, safecrlf, renameLimit)
    assert mock_run.call_count == 3


def test_configure_workspace_git_caches_dir(tmp_path) -> None:
    with patch("utils.git_utils.subprocess.run") as mock_run:
        configure_workspace_git(str(tmp_path))
        configure_workspace_git(str(tmp_path))  # second call — should be cached
    assert mock_run.call_count == 3  # not 6


def test_configure_workspace_git_different_dirs(tmp_path) -> None:
    dir_a = str(tmp_path / "a")
    dir_b = str(tmp_path / "b")
    with patch("utils.git_utils.subprocess.run") as mock_run:
        configure_workspace_git(dir_a)
        configure_workspace_git(dir_b)
    assert mock_run.call_count == 6  # 3 per dir


def test_configure_workspace_git_suppresses_subprocess_errors(tmp_path) -> None:
    """Subprocess errors in git config are swallowed (best-effort)."""
    with patch("utils.git_utils.subprocess.run", side_effect=OSError("no git")):
        # Should not raise
        configure_workspace_git(str(tmp_path))


# ── is_git_repo ───────────────────────────────────────────────────────────────

def test_is_git_repo_returns_true_on_success() -> None:
    with patch("utils.git_utils.run_git", return_value="true"):
        assert is_git_repo("/some/repo") is True


def test_is_git_repo_returns_false_on_runtime_error() -> None:
    with patch("utils.git_utils.run_git", side_effect=RuntimeError("not a repo")):
        assert is_git_repo("/not/a/repo") is False


def test_is_git_repo_returns_false_on_file_not_found() -> None:
    with patch("utils.git_utils.run_git", side_effect=FileNotFoundError("git not found")):
        assert is_git_repo("/any/path") is False
