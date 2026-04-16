"""Extended unit tests for utils/memory.py — log helpers and context builder."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch


from utils.memory import (
    _next_adr_number,
    _read_markdown_context,
    get_context_for_task,
    log_bug,
    log_decision,
    log_issue,
    log_key_fact,
)


# ── _next_adr_number ──────────────────────────────────────────────────────────

def test_next_adr_number_no_file_returns_1(tmp_path: Path) -> None:
    assert _next_adr_number(str(tmp_path)) == 1


def test_next_adr_number_empty_file_returns_1(tmp_path: Path) -> None:
    orch = tmp_path / ".orchestrator"
    orch.mkdir()
    (orch / "decisions.md").write_text("# Decisions\n\n")
    assert _next_adr_number(str(tmp_path)) == 1


def test_next_adr_number_single_adr(tmp_path: Path) -> None:
    orch = tmp_path / ".orchestrator"
    orch.mkdir()
    (orch / "decisions.md").write_text("### ADR-001: Some decision\n")
    assert _next_adr_number(str(tmp_path)) == 2


def test_next_adr_number_multiple_adrs(tmp_path: Path) -> None:
    orch = tmp_path / ".orchestrator"
    orch.mkdir()
    content = "### ADR-001: First\n### ADR-002: Second\n### ADR-005: Jumped\n"
    (orch / "decisions.md").write_text(content)
    assert _next_adr_number(str(tmp_path)) == 6


def test_next_adr_number_returns_1_on_exception(tmp_path: Path) -> None:
    """If read_text raises, returns 1 gracefully."""
    orch = tmp_path / ".orchestrator"
    orch.mkdir()
    path = orch / "decisions.md"
    path.write_text("ADR-003")
    with patch.object(Path, "read_text", side_effect=OSError("disk error")):
        assert _next_adr_number(str(tmp_path)) == 1


# ── log_bug ───────────────────────────────────────────────────────────────────

def test_log_bug_creates_entry(tmp_path: Path) -> None:
    orch = tmp_path / ".orchestrator"
    orch.mkdir()
    (orch / "bugs.md").write_text("# Bugs\n")
    with patch("utils.memory.datetime") as mock_dt:
        mock_dt.now.return_value.strftime.return_value = "2024-01-15"
        log_bug(str(tmp_path), "Login fails on empty password")
    content = (orch / "bugs.md").read_text()
    assert "2024-01-15" in content
    assert "Login fails" in content


def test_log_bug_includes_optional_fields(tmp_path: Path) -> None:
    orch = tmp_path / ".orchestrator"
    orch.mkdir()
    (orch / "bugs.md").write_text("# Bugs\n")
    log_bug(
        str(tmp_path),
        "Crash on startup",
        root_cause="Missing env var",
        solution="Add default",
        prevention="Add validation",
    )
    content = (orch / "bugs.md").read_text()
    assert "Root Cause" in content
    assert "Missing env var" in content
    assert "Solution" in content
    assert "Add default" in content
    assert "Prevention" in content
    assert "Add validation" in content


def test_log_bug_omits_empty_optional_fields(tmp_path: Path) -> None:
    orch = tmp_path / ".orchestrator"
    orch.mkdir()
    (orch / "bugs.md").write_text("# Bugs\n")
    log_bug(str(tmp_path), "Minor issue")
    content = (orch / "bugs.md").read_text()
    assert "Root Cause" not in content
    assert "Solution" not in content
    assert "Prevention" not in content


def test_log_bug_truncates_long_title(tmp_path: Path) -> None:
    orch = tmp_path / ".orchestrator"
    orch.mkdir()
    (orch / "bugs.md").write_text("# Bugs\n")
    long_issue = "x" * 100
    log_bug(str(tmp_path), long_issue)
    content = (orch / "bugs.md").read_text()
    # Header title should be ≤70 chars
    header_line = [l for l in content.splitlines() if l.startswith("###")][0]
    # The title part after "YYYY-MM-DD - " should be truncated
    assert len(header_line) < 120


# ── log_decision ──────────────────────────────────────────────────────────────

def test_log_decision_creates_adr_entry(tmp_path: Path) -> None:
    orch = tmp_path / ".orchestrator"
    orch.mkdir()
    (orch / "decisions.md").write_text("# Decisions\n")
    log_decision(
        str(tmp_path),
        title="Use PostgreSQL",
        context="We need a relational DB",
        decision="PostgreSQL chosen for ACID guarantees",
        date="2024-03-01",
    )
    content = (orch / "decisions.md").read_text()
    assert "ADR-001" in content
    assert "Use PostgreSQL" in content
    assert "2024-03-01" in content
    assert "ACID guarantees" in content


def test_log_decision_auto_increments_adr(tmp_path: Path) -> None:
    orch = tmp_path / ".orchestrator"
    orch.mkdir()
    (orch / "decisions.md").write_text("### ADR-003: Existing\n")
    log_decision(
        str(tmp_path),
        title="New decision",
        context="ctx",
        decision="dec",
        date="2024-03-02",
    )
    content = (orch / "decisions.md").read_text()
    assert "ADR-004" in content


def test_log_decision_includes_alternatives(tmp_path: Path) -> None:
    orch = tmp_path / ".orchestrator"
    orch.mkdir()
    (orch / "decisions.md").write_text("")
    log_decision(
        str(tmp_path),
        title="Title",
        context="ctx",
        decision="dec",
        alternatives="MySQL was considered",
        date="2024-01-01",
    )
    content = (orch / "decisions.md").read_text()
    assert "Alternatives Considered" in content
    assert "MySQL" in content


def test_log_decision_omits_empty_alternatives(tmp_path: Path) -> None:
    orch = tmp_path / ".orchestrator"
    orch.mkdir()
    (orch / "decisions.md").write_text("")
    log_decision(
        str(tmp_path), title="T", context="c", decision="d", date="2024-01-01"
    )
    content = (orch / "decisions.md").read_text()
    assert "Alternatives Considered" not in content


def test_log_decision_includes_consequences(tmp_path: Path) -> None:
    orch = tmp_path / ".orchestrator"
    orch.mkdir()
    (orch / "decisions.md").write_text("")
    log_decision(
        str(tmp_path),
        title="T",
        context="c",
        decision="d",
        consequences="Requires migration",
        date="2024-01-01",
    )
    content = (orch / "decisions.md").read_text()
    assert "Consequences" in content
    assert "migration" in content


# ── log_key_fact ──────────────────────────────────────────────────────────────

def test_log_key_fact_appends_entry(tmp_path: Path) -> None:
    orch = tmp_path / ".orchestrator"
    orch.mkdir()
    (orch / "key_facts.md").write_text("# Key Facts\n")
    log_key_fact(str(tmp_path), category="Database", fact="Uses PostgreSQL 15", date="2024-01-01")
    content = (orch / "key_facts.md").read_text()
    assert "Database" in content
    assert "PostgreSQL 15" in content
    assert "2024-01-01" in content


def test_log_key_fact_multiple_entries(tmp_path: Path) -> None:
    orch = tmp_path / ".orchestrator"
    orch.mkdir()
    (orch / "key_facts.md").write_text("")
    log_key_fact(str(tmp_path), category="DB", fact="fact1", date="2024-01-01")
    log_key_fact(str(tmp_path), category="API", fact="fact2", date="2024-01-02")
    content = (orch / "key_facts.md").read_text()
    assert "fact1" in content
    assert "fact2" in content


# ── log_issue ─────────────────────────────────────────────────────────────────

def test_log_issue_creates_entry(tmp_path: Path) -> None:
    orch = tmp_path / ".orchestrator"
    orch.mkdir()
    (orch / "issues.md").write_text("# Issues\n")
    with patch("utils.memory.datetime") as mock_dt:
        mock_dt.now.return_value.strftime.return_value = "2024-02-10"
        log_issue(str(tmp_path), task="Implement login", outcome="Completed")
    content = (orch / "issues.md").read_text()
    assert "Implement login" in content
    assert "Completed" in content
    assert "2024-02-10" in content


def test_log_issue_with_ticket_id(tmp_path: Path) -> None:
    orch = tmp_path / ".orchestrator"
    orch.mkdir()
    (orch / "issues.md").write_text("")
    log_issue(str(tmp_path), task="Fix bug", outcome="Done", ticket_id="JIRA-42")
    content = (orch / "issues.md").read_text()
    assert "JIRA-42" in content


def test_log_issue_with_url_and_notes(tmp_path: Path) -> None:
    orch = tmp_path / ".orchestrator"
    orch.mkdir()
    (orch / "issues.md").write_text("")
    log_issue(
        str(tmp_path),
        task="Add feature",
        outcome="Merged",
        url="https://github.com/org/repo/pull/1",
        notes="Required extra testing",
    )
    content = (orch / "issues.md").read_text()
    assert "URL" in content
    assert "Notes" in content


def test_log_issue_omits_empty_url_notes(tmp_path: Path) -> None:
    orch = tmp_path / ".orchestrator"
    orch.mkdir()
    (orch / "issues.md").write_text("")
    log_issue(str(tmp_path), task="Task", outcome="Done")
    content = (orch / "issues.md").read_text()
    assert "URL" not in content
    assert "Notes" not in content


# ── get_context_for_task ──────────────────────────────────────────────────────

def test_get_context_for_task_empty_memory_returns_empty(tmp_path: Path) -> None:
    """No memory files → empty string returned."""
    result = get_context_for_task(str(tmp_path), "build something")
    assert result == ""


def test_get_context_for_task_with_decisions(tmp_path: Path) -> None:
    memory = {
        "architecture_decisions": [{"text": "Use PostgreSQL for storage"}],
        "codebase_conventions": [],
        "past_mistakes": [],
        "past_tasks": [],
    }
    with (
        patch("utils.memory.load_memory", return_value=memory),
        patch("utils.memory.search_past_tasks", return_value=[]),
        patch("utils.memory._read_markdown_context", return_value=""),
    ):
        result = get_context_for_task(str(tmp_path), "add database")
    assert "ARCHITECTURE DECISIONS" in result
    assert "PostgreSQL" in result


def test_get_context_for_task_with_conventions(tmp_path: Path) -> None:
    memory = {
        "architecture_decisions": [],
        "codebase_conventions": [{"text": "Use black for formatting"}],
        "past_mistakes": [],
        "past_tasks": [],
    }
    with (
        patch("utils.memory.load_memory", return_value=memory),
        patch("utils.memory.search_past_tasks", return_value=[]),
        patch("utils.memory._read_markdown_context", return_value=""),
    ):
        result = get_context_for_task(str(tmp_path), "format code")
    assert "CODEBASE CONVENTIONS" in result
    assert "black" in result


def test_get_context_for_task_with_past_tasks(tmp_path: Path) -> None:
    memory = {
        "architecture_decisions": [],
        "codebase_conventions": [],
        "past_mistakes": [],
        "past_tasks": [],
    }
    related = [{"date": "2024-01-01", "task": "add auth", "outcome": "Completed"}]
    with (
        patch("utils.memory.load_memory", return_value=memory),
        patch("utils.memory.search_past_tasks", return_value=related),
        patch("utils.memory._read_markdown_context", return_value=""),
    ):
        result = get_context_for_task(str(tmp_path), "add login")
    assert "RELATED PAST TASKS" in result
    assert "add auth" in result


def test_get_context_for_task_limits_to_5_items(tmp_path: Path) -> None:
    items = [{"text": f"decision {i}"} for i in range(10)]
    memory = {
        "architecture_decisions": items,
        "codebase_conventions": [],
        "past_mistakes": [],
        "past_tasks": [],
    }
    with (
        patch("utils.memory.load_memory", return_value=memory),
        patch("utils.memory.search_past_tasks", return_value=[]),
        patch("utils.memory._read_markdown_context", return_value=""),
    ):
        result = get_context_for_task(str(tmp_path), "task")
    # Only last 5 items shown — "decision 5" through "decision 9"
    assert "decision 9" in result
    assert "decision 0" not in result


# ── _read_markdown_context ────────────────────────────────────────────────────

def test_read_markdown_context_no_files_returns_empty(tmp_path: Path) -> None:
    result = _read_markdown_context(str(tmp_path), "build authentication system")
    assert result == ""


def test_read_markdown_context_matches_keywords(tmp_path: Path) -> None:
    orch = tmp_path / ".orchestrator"
    orch.mkdir()
    # Write bugs.md with an entry matching "authentication"
    (orch / "bugs.md").write_text(
        "# Bugs\n\n### 2024-01-01 - Auth bug\n- **Issue**: authentication failed\n"
    )
    (orch / "decisions.md").write_text("# Decisions\n")
    (orch / "key_facts.md").write_text("# Key Facts\n")
    result = _read_markdown_context(str(tmp_path), "fix authentication bug")
    assert "RELEVANT BUGS" in result
    assert "Auth bug" in result


def test_read_markdown_context_no_keyword_match_returns_empty(tmp_path: Path) -> None:
    orch = tmp_path / ".orchestrator"
    orch.mkdir()
    (orch / "bugs.md").write_text("# Bugs\n\n### 2024-01-01 - Unrelated\n- Something else\n")
    result = _read_markdown_context(str(tmp_path), "xyz zzz qqq")
    # Short/unrecognized keywords → returns ""
    assert result == ""


def test_read_markdown_context_matches_decisions(tmp_path: Path) -> None:
    orch = tmp_path / ".orchestrator"
    orch.mkdir()
    (orch / "bugs.md").write_text("# Bugs\n")
    (orch / "decisions.md").write_text(
        "# Decisions\n\n### ADR-001: Use PostgreSQL\nWe chose PostgreSQL for storage.\n"
    )
    (orch / "key_facts.md").write_text("# Key Facts\n")
    result = _read_markdown_context(str(tmp_path), "database storage decision")
    assert "RELEVANT DECISIONS" in result


def test_read_markdown_context_matches_key_facts(tmp_path: Path) -> None:
    orch = tmp_path / ".orchestrator"
    orch.mkdir()
    (orch / "bugs.md").write_text("# Bugs\n")
    (orch / "decisions.md").write_text("# Decisions\n")
    (orch / "key_facts.md").write_text(
        "# Key Facts\n\n### Configuration\n- [2024-01-01] Redis used for caching\n"
    )
    result = _read_markdown_context(str(tmp_path), "update caching configuration")
    assert "KEY FACTS" in result
