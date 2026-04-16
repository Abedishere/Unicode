"""Unit tests for _is_usage_limit() in utils/runner.py."""
from __future__ import annotations

import pytest

from utils.runner import _is_usage_limit


@pytest.mark.parametrize("stdout,stderr", [
    ("claude.ai/api/limits exceeded", ""),
    ("", "rate_limit_error occurred"),
    ("overloaded_error: server busy", ""),
    ("usage limit reached for this account", ""),
    ("error 529 too many requests from client", ""),
    ("too many requests to the API", ""),
    ("request rate limit hit", ""),
    ("quota exceeded for the current billing period", ""),
    ("maximum context length exceeded for this model", ""),
    # Limit signal in stderr only
    ("normal stdout output", "rate_limit_error in trace"),
    # Case-insensitive
    ("USAGE LIMIT REACHED", ""),
    ("", "QUOTA EXCEEDED"),
    ("", "OVERLOADED_ERROR"),
])
def test_is_limit_true(stdout: str, stderr: str) -> None:
    assert _is_usage_limit(stdout, stderr) is True


@pytest.mark.parametrize("stdout,stderr", [
    ("Task completed successfully.", ""),
    ("", ""),
    ("error: file not found", ""),
    ("SyntaxError: unexpected token at line 5", ""),
    ("Connection refused on port 8080", "timeout after 30s"),
    ("def add(a, b): return a + b", ""),
    ("Process exited with code 1", "Traceback (most recent call last)"),
])
def test_is_limit_false(stdout: str, stderr: str) -> None:
    assert _is_usage_limit(stdout, stderr) is False
