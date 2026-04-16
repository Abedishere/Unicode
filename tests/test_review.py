"""Unit tests for phases/review.py — diff summarisation helpers."""
from __future__ import annotations

from unittest.mock import MagicMock, patch


from phases.review import (
    _determine_verdict,
    _extract_file_diff,
    _handle_full_diff_request,
    _qwen_primary_review,
    _summarize_diff,
)


_SIMPLE_DIFF = """\
diff --git a/auth.py b/auth.py
index abc..def 100644
--- a/auth.py
+++ b/auth.py
@@ -1,5 +1,8 @@
+def login(user, password):
+    return True
+
-def process(data):
-    pass
+def process(data, timeout=30):
+    return data
"""

_MULTI_FILE_DIFF = """\
diff --git a/auth.py b/auth.py
index abc..def 100644
--- a/auth.py
+++ b/auth.py
@@ -1,3 +1,5 @@
+def login(user):
+    pass
 def logout():
-    pass
+    return True

diff --git a/models.py b/models.py
index 111..222 100644
--- a/models.py
+++ b/models.py
@@ -1,3 +1,5 @@
+class User:
+    pass
 class Session:
     pass
"""

_CONFIG_DIFF = """\
diff --git a/config.yaml b/config.yaml
index abc..def 100644
--- a/config.yaml
+++ b/config.yaml
@@ -1,2 +1,3 @@
 key: value
+new_key: new_value
-old_key: old_value
"""


# ── _summarize_diff ───────────────────────────────────────────────────────────

def test_summarize_diff_empty_returns_empty() -> None:
    assert _summarize_diff("") == ""


def test_summarize_diff_single_file_header() -> None:
    result = _summarize_diff(_SIMPLE_DIFF)
    assert "FILES CHANGED: 1" in result
    assert "auth.py" in result


def test_summarize_diff_counts_added_lines() -> None:
    result = _summarize_diff(_SIMPLE_DIFF)
    # Some lines added (login def + return True + modified process lines)
    assert "+" in result


def test_summarize_diff_detects_added_function() -> None:
    result = _summarize_diff(_SIMPLE_DIFF)
    assert "Added: login" in result


def test_summarize_diff_detects_modified_function() -> None:
    # process appears in both + and - def lines → Modified
    result = _summarize_diff(_SIMPLE_DIFF)
    assert "Modified: process" in result


def test_summarize_diff_multi_file() -> None:
    result = _summarize_diff(_MULTI_FILE_DIFF)
    assert "FILES CHANGED: 2" in result
    assert "auth.py" in result
    assert "models.py" in result


def test_summarize_diff_config_file_no_function_names() -> None:
    result = _summarize_diff(_CONFIG_DIFF)
    assert "config.yaml" in result
    assert "(configuration/data changes)" in result


def test_summarize_diff_detects_added_class() -> None:
    result = _summarize_diff(_MULTI_FILE_DIFF)
    assert "Added: User" in result


def test_summarize_diff_no_header_section_skipped() -> None:
    """A diff section without a 'diff --git' header is skipped gracefully."""
    diff = "\nsome random text\n"
    result = _summarize_diff(diff)
    assert "FILES CHANGED: 0" in result


# ── _extract_file_diff ────────────────────────────────────────────────────────

def test_extract_file_diff_finds_file() -> None:
    result = _extract_file_diff(_MULTI_FILE_DIFF, ["auth.py"])
    assert "auth.py" in result
    assert "models.py" not in result


def test_extract_file_diff_multiple_files() -> None:
    result = _extract_file_diff(_MULTI_FILE_DIFF, ["auth.py", "models.py"])
    assert "auth.py" in result
    assert "models.py" in result


def test_extract_file_diff_missing_file_returns_empty() -> None:
    result = _extract_file_diff(_MULTI_FILE_DIFF, ["nonexistent.py"])
    assert result == ""


def test_extract_file_diff_empty_diff() -> None:
    assert _extract_file_diff("", ["auth.py"]) == ""


# ── _determine_verdict ────────────────────────────────────────────────────────

def test_determine_verdict_approved_line() -> None:
    assert _determine_verdict("APPROVED\nLooks great.") is True


def test_determine_verdict_approved_case_insensitive() -> None:
    assert _determine_verdict("approved") is True


def test_determine_verdict_changes_requested() -> None:
    assert _determine_verdict("CHANGES_REQUESTED\n1. Fix the bug.") is False


def test_determine_verdict_both_signals_changes_wins() -> None:
    # CHANGES_REQUESTED present → False regardless of APPROVED
    assert _determine_verdict("APPROVED\nCHANGES_REQUESTED\n1. Fix it.") is False


def test_determine_verdict_looks_good() -> None:
    assert _determine_verdict("lgtm, no issues found") is True


def test_determine_verdict_ship_it() -> None:
    assert _determine_verdict("ship it") is True


def test_determine_verdict_no_issues() -> None:
    assert _determine_verdict("no issues detected") is True


def test_determine_verdict_changes_requested_string() -> None:
    # CHANGES_REQUESTED keyword → False
    assert _determine_verdict("CHANGES_REQUESTED\nFix the null check.") is False


def test_determine_verdict_empty_string() -> None:
    # No CHANGES_REQUESTED → treated as approved
    assert _determine_verdict("") is True


# ── _handle_full_diff_request ─────────────────────────────────────────────────

def test_handle_full_diff_no_request_returns_original() -> None:
    review = "APPROVED\nLooks good."
    result = _handle_full_diff_request(review, _MULTI_FILE_DIFF, lambda p: "new", "Test")
    assert result == review


def test_handle_full_diff_calls_agent_with_extracted_diff() -> None:
    review = "NEED_FULL_DIFF: auth.py\nMore context needed."
    calls = []

    def fake_query(prompt: str) -> str:
        calls.append(prompt)
        return "APPROVED after full diff"

    with (
        patch("phases.review.log_info"),
        patch("phases.review.log_agent"),
    ):
        result = _handle_full_diff_request(review, _MULTI_FILE_DIFF, fake_query, "TestAgent")
    assert result == "APPROVED after full diff"
    assert len(calls) == 1
    assert "auth.py" in calls[0]


def test_handle_full_diff_file_not_in_diff_returns_original() -> None:
    review = "NEED_FULL_DIFF: nonexistent.py\n"
    with patch("phases.review.log_info"):
        result = _handle_full_diff_request(review, _MULTI_FILE_DIFF, lambda p: "new", "Agent")
    assert result == review


def test_handle_full_diff_agent_raises_returns_original() -> None:
    review = "NEED_FULL_DIFF: auth.py\n"

    def failing_query(prompt: str) -> str:
        raise RuntimeError("agent failed")

    with (
        patch("phases.review.log_info"),
        patch("phases.review.log_agent"),
    ):
        result = _handle_full_diff_request(review, _MULTI_FILE_DIFF, failing_query, "Agent")
    assert result == review


def test_handle_full_diff_text_only_preamble_included() -> None:
    review = "NEED_FULL_DIFF: auth.py\n"
    received = []

    def capture_query(prompt: str) -> str:
        received.append(prompt)
        return "APPROVED"

    with (
        patch("phases.review.log_info"),
        patch("phases.review.log_agent"),
    ):
        _handle_full_diff_request(
            review, _MULTI_FILE_DIFF, capture_query, "Agent", text_only_preamble=True
        )
    assert "TEXT-ONLY TASK" in received[0]


# ── _qwen_primary_review ──────────────────────────────────────────────────────

def test_qwen_primary_review_approved() -> None:
    qwen = MagicMock()
    qwen.review_query.return_value = "APPROVED\nAll good."
    with (
        patch("phases.review.log_agent"),
        patch("phases.review.log_info"),
        patch("phases.review.console"),
    ):
        review, approved = _qwen_primary_review(
            qwen, diff="", task="build auth", plan="create auth.py", iteration=1, max_iterations=2
        )
    assert approved is True
    assert "APPROVED" in review


def test_qwen_primary_review_changes_requested() -> None:
    qwen = MagicMock()
    qwen.review_query.return_value = "CHANGES_REQUESTED\n1. Missing validation."
    with (
        patch("phases.review.log_agent"),
        patch("phases.review.log_info"),
        patch("phases.review.console"),
    ):
        review, approved = _qwen_primary_review(
            qwen, diff="", task="build auth", plan="create auth.py", iteration=1, max_iterations=2
        )
    assert approved is False
    assert "CHANGES_REQUESTED" in review


def test_qwen_primary_review_uses_provided_diff_summary() -> None:
    """If diff_summary is provided, _summarize_diff should not be called."""
    qwen = MagicMock()
    qwen.review_query.return_value = "APPROVED"
    with (
        patch("phases.review.log_agent"),
        patch("phases.review.log_info"),
        patch("phases.review.console"),
        patch("phases.review._summarize_diff") as mock_summarize,
    ):
        _qwen_primary_review(
            qwen,
            diff="some diff",
            task="task",
            plan="plan",
            iteration=1,
            max_iterations=1,
            diff_summary="pre-computed summary",
        )
    mock_summarize.assert_not_called()


# ── _claude_secondary_review ──────────────────────────────────────────────────

from phases.review import _claude_secondary_review


def test_claude_secondary_review_confirmed_issues() -> None:
    claude = MagicMock()
    claude.query.return_value = "CONFIRMED\n1. Null check missing in login()."
    with (
        patch("phases.review.log_info"),
        patch("phases.review.log_agent"),
        patch("phases.review.log_error"),
    ):
        review, has_issues = _claude_secondary_review(
            claude, codex_review="CHANGES_REQUESTED\n1. Null check.",
            diff="", task="build auth", plan="create auth.py"
        )
    assert has_issues is True
    assert "CONFIRMED" in review


def test_claude_secondary_review_approved_no_issues() -> None:
    claude = MagicMock()
    claude.query.return_value = "APPROVED\nAll issues were invalid."
    with (
        patch("phases.review.log_info"),
        patch("phases.review.log_agent"),
        patch("phases.review.log_error"),
    ):
        review, has_issues = _claude_secondary_review(
            claude, codex_review="CHANGES_REQUESTED\n1. Style issue.",
            diff="", task="build auth", plan="create auth.py"
        )
    assert has_issues is False


def test_claude_secondary_review_runtime_error_fallback() -> None:
    """If Claude query raises RuntimeError, falls back to Codex review."""
    claude = MagicMock()
    claude.query.side_effect = RuntimeError("claude failed")
    codex_review = "CHANGES_REQUESTED\n1. Real bug."
    with (
        patch("phases.review.log_info"),
        patch("phases.review.log_agent"),
        patch("phases.review.log_error"),
    ):
        review, has_issues = _claude_secondary_review(
            claude, codex_review=codex_review,
            diff="", task="task", plan="plan"
        )
    assert review == codex_review
    assert has_issues is True


def test_claude_secondary_review_usage_limit_fallback() -> None:
    """UsageLimitReached also causes fallback to Codex review."""
    from utils.runner import UsageLimitReached
    claude = MagicMock()
    claude.query.side_effect = UsageLimitReached("limit")
    codex_review = "CHANGES_REQUESTED\n1. Something."
    with (
        patch("phases.review.log_info"),
        patch("phases.review.log_agent"),
        patch("phases.review.log_error"),
    ):
        review, has_issues = _claude_secondary_review(
            claude, codex_review=codex_review,
            diff="", task="task", plan="plan"
        )
    assert review == codex_review


def test_claude_secondary_review_uses_provided_diff_summary() -> None:
    claude = MagicMock()
    claude.query.return_value = "APPROVED"
    with (
        patch("phases.review.log_info"),
        patch("phases.review.log_agent"),
        patch("phases.review.log_error"),
        patch("phases.review._summarize_diff") as mock_sum,
    ):
        _claude_secondary_review(
            claude, codex_review="CHANGES_REQUESTED",
            diff="raw diff", task="t", plan="p",
            diff_summary="pre-computed",
        )
    mock_sum.assert_not_called()
