"""Unit tests for phases/clarify.py."""
from __future__ import annotations

from unittest.mock import MagicMock, patch


from phases.clarify import _collect_input, relay_agent_questions, run_interpreter


def _stub_interpreter(responses: list[str]) -> MagicMock:
    interp = MagicMock()
    interp.name = "Interpreter"
    calls = [0]

    def query(prompt: str) -> str:
        resp = responses[min(calls[0], len(responses) - 1)]
        calls[0] += 1
        return resp

    interp.query.side_effect = query
    return interp


# ── _collect_input ────────────────────────────────────────────────────────────

def test_collect_input_returns_user_text() -> None:
    with patch("phases.clarify.click.prompt", return_value="build a login system"):
        result = _collect_input()
    assert result == "build a login system"


def test_collect_input_eof_returns_empty() -> None:
    with patch("phases.clarify.click.prompt", side_effect=EOFError):
        result = _collect_input()
    assert result == ""


def test_collect_input_abort_returns_empty() -> None:
    import click
    with patch("phases.clarify.click.prompt", side_effect=click.Abort):
        result = _collect_input()
    assert result == ""


# ── run_interpreter — immediate READY ────────────────────────────────────────

def test_run_interpreter_immediate_ready_returns_brief() -> None:
    interp = _stub_interpreter(["READY Build a login page with email and password."])
    with (
        patch("phases.clarify.log_phase"),
        patch("phases.clarify.log_info"),
        patch("phases.clarify.console"),
    ):
        result = run_interpreter("build a login", interp)
    assert "login" in result.lower() or "Build" in result
    assert interp.query.call_count == 1


def test_run_interpreter_immediate_ready_empty_brief_falls_back_to_task() -> None:
    """If READY has no following text, uses the original task."""
    interp = _stub_interpreter(["READY"])
    with (
        patch("phases.clarify.log_phase"),
        patch("phases.clarify.log_info"),
        patch("phases.clarify.console"),
    ):
        result = run_interpreter("original task text", interp)
    assert result == "original task text"


# ── run_interpreter — clarification loop ─────────────────────────────────────

def test_run_interpreter_asks_question_then_ready() -> None:
    interp = _stub_interpreter([
        "What language? Python or Go?",  # first turn — asks question
        "READY Build a Python web app.",  # after user answers
    ])
    with (
        patch("phases.clarify.log_phase"),
        patch("phases.clarify.log_info"),
        patch("phases.clarify.console"),
        patch("phases.clarify.click.prompt", return_value="Python please"),
    ):
        result = run_interpreter("build a web app", interp)
    assert "Python" in result


def test_run_interpreter_user_says_go_breaks_loop() -> None:
    """User typing 'go' skips the rest of the loop."""
    interp = _stub_interpreter([
        "What stack?",      # first turn
        "stub brief",       # compile turn
    ])
    with (
        patch("phases.clarify.log_phase"),
        patch("phases.clarify.log_info"),
        patch("phases.clarify.console"),
        patch("phases.clarify.click.prompt", return_value="go"),
    ):
        run_interpreter("build something", interp)
    # Interpreter called twice: first question + compile brief
    assert interp.query.call_count == 2


def test_run_interpreter_empty_user_input_breaks_loop() -> None:
    """Empty user input ends the conversation loop."""
    interp = _stub_interpreter([
        "Any more details?",
        "compiled brief",
    ])
    with (
        patch("phases.clarify.log_phase"),
        patch("phases.clarify.log_info"),
        patch("phases.clarify.console"),
        patch("phases.clarify.click.prompt", return_value=""),
    ):
        run_interpreter("build a thing", interp)
    assert interp.query.call_count == 2


def test_run_interpreter_ready_mid_loop() -> None:
    """READY can come from the follow-up turn after user provides input."""
    interp = _stub_interpreter([
        "What features?",
        "READY Create a REST API with authentication.",
    ])
    with (
        patch("phases.clarify.log_phase"),
        patch("phases.clarify.log_info"),
        patch("phases.clarify.console"),
        patch("phases.clarify.click.prompt", return_value="auth and CRUD"),
    ):
        result = run_interpreter("build an API", interp)
    assert "REST API" in result


def test_run_interpreter_max_turns_compiles_brief() -> None:
    """After 4 turns without READY, compiles a brief."""
    interp = _stub_interpreter([
        "Q1?", "Q2?", "Q3?", "Q4?",  # 4 turns of questions (loop)
        "Final compiled brief.",       # compile turn (5th+)
    ])
    # Give 4 non-empty, non-go answers
    user_responses = iter(["a1", "a2", "a3", "a4"])
    with (
        patch("phases.clarify.log_phase"),
        patch("phases.clarify.log_info"),
        patch("phases.clarify.console"),
        patch("phases.clarify.click.prompt", side_effect=user_responses),
    ):
        result = run_interpreter("complex task", interp)
    # 1 first query + 4 loop follow-ups + 1 compile = 6
    assert interp.query.call_count == 6
    assert "Final compiled brief" in result


# ── relay_agent_questions ─────────────────────────────────────────────────────

def test_relay_agent_questions_returns_answers() -> None:
    with (
        patch("phases.clarify.console"),
        patch("phases.clarify.click.prompt", side_effect=["line one", "line two", "", ""]),
    ):
        result = relay_agent_questions("What DB?", MagicMock(), "build app")
    assert "line one" in result
    assert "line two" in result


def test_relay_agent_questions_empty_returns_fallback() -> None:
    with (
        patch("phases.clarify.console"),
        patch("phases.clarify.click.prompt", side_effect=["", ""]),
    ):
        result = relay_agent_questions("What DB?", MagicMock(), "build app")
    assert "proceed with your best judgment" in result


def test_relay_agent_questions_eof_returns_fallback() -> None:
    with (
        patch("phases.clarify.console"),
        patch("phases.clarify.click.prompt", side_effect=EOFError),
    ):
        result = relay_agent_questions("What DB?", MagicMock(), "build app")
    assert "proceed with your best judgment" in result
