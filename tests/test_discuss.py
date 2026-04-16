"""Unit tests for phases/discuss.py."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from phases.discuss import (
    _ask_user,
    _build_prompt,
    _format_discussion_block,
    _has_agreement,
    _has_declined,
    _has_user_question,
    _summarize_old_history,
    run_discussion,
)


# ── Boolean detectors ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("text", [
    "@User what do you prefer?",
    "Could you clarify the requirements?",
    "Would you like to proceed?",
    "Do you want a REST or GraphQL API?",
    "What do you think about this approach?",
    "Please confirm this is the right direction.",
    "Need your input on the database choice.",
])
def test_has_user_question_true(text: str) -> None:
    assert _has_user_question(text) is True


@pytest.mark.parametrize("text", [
    "I recommend using PostgreSQL. AGREED",
    "We should create auth.py with JWT. AGREED",
    "The plan looks solid.",
    "",
])
def test_has_user_question_false(text: str) -> None:
    assert _has_user_question(text) is False


def test_has_declined_true() -> None:
    assert _has_declined("DECLINED — missing error handling") is True


def test_has_declined_false() -> None:
    assert _has_declined("I AGREED with the plan") is False
    assert _has_declined("") is False


def test_has_agreement_agreed() -> None:
    assert _has_agreement("This looks great. AGREED") is True


def test_has_agreement_i_agree() -> None:
    assert _has_agreement("I agree with your proposal.") is True


def test_has_agreement_false() -> None:
    assert _has_agreement("I'm not sure yet.") is False
    assert _has_agreement("DECLINED") is False


# ── _summarize_old_history ────────────────────────────────────────────────────

def test_summarize_short_history_unchanged() -> None:
    history = [
        {"agent": "Codex", "message": "msg1"},
        {"agent": "Claude", "message": "msg2"},
    ]
    summary, recent = _summarize_old_history(history, keep_recent=4)
    assert summary == ""
    assert recent == history


def test_summarize_long_history() -> None:
    history = [{"agent": f"Agent{i}", "message": f"message {i}"} for i in range(8)]
    summary, recent = _summarize_old_history(history, keep_recent=4)
    assert summary != ""
    assert len(recent) == 4
    assert recent == history[-4:]


def test_summarize_truncates_long_messages() -> None:
    long_msg = "word " * 100
    history = [{"agent": "Codex", "message": long_msg} for _ in range(6)]
    summary, _ = _summarize_old_history(history, keep_recent=2)
    # Each summary line should be truncated (≤ ~180 chars for the message part)
    for line in summary.splitlines():
        assert len(line) < 300


def test_summarize_exact_boundary() -> None:
    """Exactly keep_recent entries → summary is empty."""
    history = [{"agent": "A", "message": "m"} for _ in range(4)]
    summary, recent = _summarize_old_history(history, keep_recent=4)
    assert summary == ""
    assert len(recent) == 4


# ── _format_discussion_block ──────────────────────────────────────────────────

def test_format_discussion_block_short_history() -> None:
    history = [{"agent": "Codex", "message": "hello"}]
    result = _format_discussion_block(history)
    assert "<discussion>" in result
    assert "hello" in result
    assert "EARLIER DISCUSSION" not in result


def test_format_discussion_block_long_history() -> None:
    history = [{"agent": f"A{i}", "message": f"msg{i}"} for i in range(10)]
    result = _format_discussion_block(history)
    assert "EARLIER DISCUSSION" in result
    assert "RECENT DISCUSSION" in result


# ── _build_prompt ─────────────────────────────────────────────────────────────

def test_build_prompt_basic() -> None:
    result = _build_prompt("build auth", [], "Claude", "Codex", 3)
    assert "build auth" in result
    assert "Claude" in result
    assert "Codex" in result


def test_build_prompt_with_repo_map() -> None:
    result = _build_prompt("task", [], "Claude", "Codex", 2, repo_map="PROJECT STRUCTURE:\n  app.py")
    assert "<codebase>" in result
    assert "app.py" in result


def test_build_prompt_without_repo_map() -> None:
    result = _build_prompt("task", [], "Claude", "Codex", 2, repo_map="")
    assert "<codebase>" not in result


def test_build_prompt_with_skills_context() -> None:
    result = _build_prompt("task", [], "Claude", "Codex", 2, skills_context="use pytest")
    assert "use pytest" in result


def test_build_prompt_without_skills_context() -> None:
    result = _build_prompt("task", [], "Claude", "Codex", 2, skills_context="")
    assert "<skills>" not in result


def test_build_prompt_includes_history() -> None:
    history = [{"agent": "Codex", "message": "I suggest using Redis."}]
    result = _build_prompt("task", history, "Claude", "Codex", 2)
    assert "Redis" in result


# ── run_discussion ────────────────────────────────────────────────────────────

def _mock_agent(name: str, responses: list[str]) -> MagicMock:
    agent = MagicMock()
    agent.name = name
    call_count = [0]

    def query(prompt: str) -> str:
        resp = responses[min(call_count[0], len(responses) - 1)]
        call_count[0] += 1
        return resp

    agent.query.side_effect = query
    return agent


def test_run_discussion_both_agree_exits_early() -> None:
    claude = _mock_agent("Claude", ["I agree with your plan. AGREED"])
    codex = _mock_agent("Codex", ["We should use JWT. AGREED"])
    history, agreed = run_discussion("build auth", claude, codex, max_rounds=3)
    assert agreed is True
    # Should exit after round 1 — not all 3 rounds
    assert len(history) < 9


def test_run_discussion_no_agreement_runs_all_rounds() -> None:
    claude = _mock_agent("Claude", ["I'm not sure about this."])
    codex = _mock_agent("Codex", ["I disagree with that approach."])
    history, agreed = run_discussion("build auth", claude, codex, max_rounds=2)
    assert agreed is False


def test_run_discussion_returns_history() -> None:
    claude = _mock_agent("Claude", ["AGREED"])
    codex = _mock_agent("Codex", ["Let's use JWT. AGREED"])
    history, _ = run_discussion("task", claude, codex, max_rounds=2)
    assert isinstance(history, list)
    assert all(isinstance(h, dict) for h in history)
    assert all("agent" in h and "message" in h for h in history)


def test_run_discussion_with_user_context() -> None:
    claude = _mock_agent("Claude", ["AGREED"])
    codex = _mock_agent("Codex", ["AGREED"])
    history, _ = run_discussion(
        "task", claude, codex, max_rounds=2,
        user_context="Please use PostgreSQL."
    )
    # User context should appear in history
    assert any(h["agent"] == "User" for h in history)


def test_run_discussion_declined_continues() -> None:
    """If Codex returns DECLINED after Claude's AGREED, discussion continues."""
    # Round 1: Codex proposes, Claude agrees, Codex confirmation declines
    # Round 2: Both reply without agreement → ends at max_rounds
    responses_codex = [
        "Let's use JWT.",        # Round 1 Codex turn
        "DECLINED — missing error handling.",  # Round 1 confirmation
        "OK let's revisit.",     # Round 2 Codex turn
        "DECLINED still.",       # Round 2 confirmation (if reached)
    ]
    responses_claude = ["I agree with JWT. AGREED", "Hmm, let me reconsider."]
    claude = _mock_agent("Claude", responses_claude)
    codex = _mock_agent("Codex", responses_codex)
    _, agreed = run_discussion("build auth", claude, codex, max_rounds=2)
    # With DECLINED in confirmation, agreement should not be set
    assert agreed is False


# ── _ask_user ─────────────────────────────────────────────────────────────────

def test_ask_user_returns_input() -> None:
    with (
        patch("phases.discuss.console"),
        patch("phases.discuss.click.prompt", return_value="use Redis"),
    ):
        result = _ask_user("Codex", "What cache layer?")
    assert result == "use Redis"


def test_ask_user_empty_input_returns_none() -> None:
    with (
        patch("phases.discuss.console"),
        patch("phases.discuss.click.prompt", return_value=""),
    ):
        result = _ask_user("Claude", "Any preferences?")
    assert result is None


def test_ask_user_eof_returns_none() -> None:
    with (
        patch("phases.discuss.console"),
        patch("phases.discuss.click.prompt", side_effect=EOFError),
    ):
        result = _ask_user("Claude", "Clarify?")
    assert result is None


# ── run_discussion with user questions ───────────────────────────────────────

def test_run_discussion_agent_question_answered_by_user() -> None:
    """When Codex asks @User in round 2+, answer appears in history.

    Note: can_ask is only True for round_num > 1, so we need at least 2 rounds
    with the question appearing in round 2.
    """
    responses_codex = [
        "Let's use JWT.",             # Round 1 — no question yet
        "@User what do you prefer?",  # Round 2 — asks user (can_ask=True)
        "AGREED",                     # confirmation turn
    ]
    responses_claude = ["Not sure yet.", "AGREED"]
    claude = _mock_agent("Claude", responses_claude)
    codex = _mock_agent("Codex", responses_codex)

    with (
        patch("phases.discuss.log_info"),
        patch("phases.discuss.log_agent"),
        patch("phases.discuss.console"),
        patch("phases.discuss.click.prompt", return_value="I prefer PostgreSQL"),
    ):
        history, _ = run_discussion(
            "build auth", claude, codex, max_rounds=3, allow_user_questions=True
        )
    user_msgs = [h for h in history if h["agent"] == "User"]
    assert len(user_msgs) >= 1


def test_run_discussion_user_question_no_answer_skipped() -> None:
    """If user gives empty answer to agent question, nothing is added to history."""
    responses_codex = [
        "@User what do you think?",
        "AGREED",
    ]
    responses_claude = ["AGREED"]
    claude = _mock_agent("Claude", responses_claude)
    codex = _mock_agent("Codex", responses_codex)

    with (
        patch("phases.discuss.log_info"),
        patch("phases.discuss.log_agent"),
        patch("phases.discuss.console"),
        patch("phases.discuss.click.prompt", return_value=""),
    ):
        history, _ = run_discussion(
            "build auth", claude, codex, max_rounds=2, allow_user_questions=True
        )
