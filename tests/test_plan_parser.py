"""Unit tests for utils/plan_parser.py."""
from __future__ import annotations


from utils.plan_parser import StructuredPlan, is_structured, parse_plan

_FULL_PLAN = """\
## Shared Dependencies
click>=8.0, pyyaml>=6.0

## Files

### src/auth.py (CREATE)
- Implement JWT token generation
- Use PyJWT library

### src/models.py (MODIFY)
- Add User.token_hash field
"""

_NO_FILES_PLAN = """\
## Shared Dependencies
None

## Files
"""

_UNSTRUCTURED_PLAN = "Just write a hello world script."


def test_parse_full_plan_file_count() -> None:
    result = parse_plan(_FULL_PLAN)
    assert len(result.files) == 2


def test_parse_full_plan_paths() -> None:
    result = parse_plan(_FULL_PLAN)
    assert result.files[0].path == "src/auth.py"
    assert result.files[1].path == "src/models.py"


def test_parse_full_plan_actions() -> None:
    result = parse_plan(_FULL_PLAN)
    assert result.files[0].action == "CREATE"
    assert result.files[1].action == "MODIFY"


def test_parse_shared_dependencies() -> None:
    result = parse_plan(_FULL_PLAN)
    assert "click" in result.shared_dependencies


def test_parse_file_spec_content() -> None:
    result = parse_plan(_FULL_PLAN)
    assert "JWT" in result.files[0].spec


def test_is_structured_true() -> None:
    result = parse_plan(_FULL_PLAN)
    assert is_structured(result) is True


def test_is_structured_false_no_files() -> None:
    result = parse_plan(_NO_FILES_PLAN)
    assert is_structured(result) is False


def test_is_structured_false_unstructured() -> None:
    result = parse_plan(_UNSTRUCTURED_PLAN)
    assert is_structured(result) is False


def test_parse_empty_string() -> None:
    result = parse_plan("")
    assert isinstance(result, StructuredPlan)
    assert result.files == []


def test_parse_none_like_whitespace() -> None:
    result = parse_plan("   \n  ")
    assert isinstance(result, StructuredPlan)
    assert result.files == []


def test_raw_preserved() -> None:
    result = parse_plan(_FULL_PLAN)
    assert result.raw == _FULL_PLAN


def test_case_insensitive_action() -> None:
    plan = "## Files\n\n### foo.py (create)\n- do stuff\n"
    result = parse_plan(plan)
    assert len(result.files) == 1
    assert result.files[0].action == "CREATE"


def test_backtick_stripped_from_path() -> None:
    plan = "## Files\n\n### `src/foo.py` (CREATE)\n- do stuff\n"
    result = parse_plan(plan)
    assert result.files[0].path == "src/foo.py"


def test_is_structured_none() -> None:
    assert is_structured(None) is False
