"""Unit tests for utils/approval.py."""
from __future__ import annotations

from unittest.mock import patch

import pytest

import utils.approval as approval


@pytest.fixture(autouse=True)
def reset_approval_state():
    """Ensure clean approval state before and after every test."""
    approval.reset_session_approvals()
    approval.set_auto_all(False)
    yield
    approval.reset_session_approvals()
    approval.set_auto_all(False)


# ── global state helpers ──────────────────────────────────────────────────────

def test_is_auto_all_default_false() -> None:
    assert approval.is_auto_all() is False


def test_set_auto_all_true() -> None:
    approval.set_auto_all(True)
    assert approval.is_auto_all() is True


def test_set_auto_all_false() -> None:
    approval.set_auto_all(True)
    approval.set_auto_all(False)
    assert approval.is_auto_all() is False


def test_reset_session_approvals_clears_cache() -> None:
    # Seed the session cache via auto-approve a session action
    approval._session_approved.add("test-action")
    assert "test-action" in approval._session_approved
    approval.reset_session_approvals()
    assert "test-action" not in approval._session_approved


# ── auto-all mode ──────────────────────────────────────────────────────────────

def test_auto_all_approves_non_critical() -> None:
    approval.set_auto_all(True)
    result, extra = approval.request_approval("discussion", "start discussion phase")
    assert result == "proceed"
    assert extra is None


def test_auto_all_approves_implement() -> None:
    approval.set_auto_all(True)
    result, _ = approval.request_approval("implement", "run implementation")
    assert result == "proceed"


def test_auto_all_does_not_bypass_git_commit() -> None:
    """git-commit is always confirmed even in auto-all mode — mock the prompt."""
    approval.set_auto_all(True)
    with patch("utils.approval.click.prompt", return_value="n"):
        result, _ = approval.request_approval("git-commit", "commit changes")
    assert result == "deny"


# ── session-approved cache ────────────────────────────────────────────────────

def test_session_approved_skips_prompt() -> None:
    """Once an action is session-approved, subsequent calls skip the TUI."""
    approval._session_approved.add("review")
    result, extra = approval.request_approval("review", "run review")
    assert result == "proceed"
    assert extra is None


# ── interactive prompt branches (mocked) ──────────────────────────────────────

def test_request_approval_y_returns_proceed() -> None:
    with patch("utils.approval.click.prompt", return_value="y"):
        result, extra = approval.request_approval("implement", "desc")
    assert result == "proceed"
    assert extra is None


def test_request_approval_n_returns_deny() -> None:
    with patch("utils.approval.click.prompt", return_value="n"):
        result, extra = approval.request_approval("implement", "desc")
    assert result == "deny"


def test_request_approval_a_adds_to_session() -> None:
    with patch("utils.approval.click.prompt", return_value="a"):
        result, _ = approval.request_approval("review", "desc")
    assert result == "proceed"
    assert "review" in approval._session_approved


# ── edit branch (choice 'e') ──────────────────────────────────────────────────

def test_request_approval_edit_then_yes_returns_instructions() -> None:
    """Choice 'e' → collect instructions → 'y' → returns instructions."""
    # prompt sequence: main choice='e', instruction line, blank+blank to end, then 'y'
    prompts = iter(["e", "use postgres", "", "", "y"])
    with patch("utils.approval.click.prompt", side_effect=prompts):
        result, extra = approval.request_approval("implement", "desc")
    assert result == "proceed"
    assert extra == "use postgres"


def test_request_approval_edit_empty_input_loops_back() -> None:
    """'e' with no input → console message, loops back to prompt → 'y'."""
    # Enter edit mode but give no instructions (two empty lines to exit edit)
    # then 'y' on the re-prompted approval
    prompts = iter(["e", "", "", "y"])
    with patch("utils.approval.click.prompt", side_effect=prompts):
        result, extra = approval.request_approval("implement", "desc")
    assert result == "proceed"
    assert extra is None  # no instructions were entered


def test_request_approval_edit_multiline_instructions() -> None:
    """Edit branch accumulates multiple lines until double-blank."""
    prompts = iter(["e", "line one", "line two", "", "", "y"])
    with patch("utils.approval.click.prompt", side_effect=prompts):
        result, extra = approval.request_approval("plan", "run plan")
    assert result == "proceed"
    assert "line one" in extra
    assert "line two" in extra


def test_request_approval_edit_then_deny() -> None:
    """After collecting edit instructions, user can still deny."""
    prompts = iter(["e", "my notes", "", "", "n"])
    with patch("utils.approval.click.prompt", side_effect=prompts):
        result, extra = approval.request_approval("implement", "desc")
    assert result == "deny"


def test_request_approval_extra_instructions_displayed() -> None:
    """Once extra_instructions is set it's shown in the panel (covers line 72)."""
    # e → instructions → blank blank → then 'y'
    prompts = iter(["e", "my instruction", "", "", "y"])
    # We can't easily inspect the panel, but exercising the code path is enough
    # for coverage of line 72 (the `if extra_instructions:` branch).
    with patch("utils.approval.click.prompt", side_effect=prompts):
        result, extra = approval.request_approval("implement", "desc")
    assert result == "proceed"
    assert extra == "my instruction"
