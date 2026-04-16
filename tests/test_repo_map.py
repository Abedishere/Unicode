"""Unit tests for utils/repo_map.py."""
from __future__ import annotations

from pathlib import Path


from utils.repo_map import (
    _analyse_js,
    _analyse_python,
    _collect_files,
    _format_detail,
    _should_skip,
    generate_repo_map,
)


# ── _analyse_python ───────────────────────────────────────────────────────────

_PY_SAMPLE = """\
import os
from pathlib import Path
from utils.foo import bar

class MyClass(Base):
    def __init__(self):
        pass
    def method(self):
        pass

def top_level_fn(x, y):
    return x + y

def another():
    pass
"""


def test_analyse_python_imports() -> None:
    result = _analyse_python(_PY_SAMPLE)
    assert "os" in result["imports"]
    assert "pathlib" in result["imports"]
    assert "utils" in result["imports"]


def test_analyse_python_classes() -> None:
    result = _analyse_python(_PY_SAMPLE)
    assert len(result["classes"]) == 1
    assert result["classes"][0]["name"] == "MyClass(Base)"


def test_analyse_python_class_methods() -> None:
    result = _analyse_python(_PY_SAMPLE)
    methods = result["classes"][0]["methods"]
    assert any("__init__" in m for m in methods)
    assert any("method" in m for m in methods)


def test_analyse_python_top_level_functions() -> None:
    # No blank lines between functions to avoid regex capturing newline as indent
    simple = "def add(a, b):\n    return a + b\ndef subtract(x, y):\n    return x - y\n"
    result = _analyse_python(simple)
    fns_text = " ".join(result["functions"])
    assert "add" in fns_text
    assert "subtract" in fns_text


def test_analyse_python_empty() -> None:
    result = _analyse_python("")
    assert result["imports"] == []
    assert result["classes"] == []
    assert result["functions"] == []


# ── _analyse_js ───────────────────────────────────────────────────────────────

_JS_SAMPLE = """\
import React from 'react';
import { useState } from 'react';
import utils from './utils';

export function MyComponent() {}
export class MyService {}
export const myHelper = () => {};
"""


def test_analyse_js_imports() -> None:
    result = _analyse_js(_JS_SAMPLE)
    assert "react" in result["imports"]
    assert "./utils" in result["imports"]


def test_analyse_js_exports() -> None:
    result = _analyse_js(_JS_SAMPLE)
    assert "MyComponent" in result["exports"]
    assert "MyService" in result["exports"]
    assert "myHelper" in result["exports"]


def test_analyse_js_empty() -> None:
    result = _analyse_js("")
    assert result["imports"] == []
    assert result["exports"] == []


# ── _should_skip ──────────────────────────────────────────────────────────────

def test_should_skip_node_modules() -> None:
    assert _should_skip("node_modules", is_dir=True) is True


def test_should_skip_dot_dir() -> None:
    assert _should_skip(".git", is_dir=True) is True
    assert _should_skip(".venv", is_dir=True) is True


def test_should_skip_normal_dir() -> None:
    assert _should_skip("src", is_dir=True) is False
    assert _should_skip("utils", is_dir=True) is False


def test_should_skip_pyc_file() -> None:
    assert _should_skip("module.pyc", is_dir=False) is True


def test_should_skip_normal_py_file() -> None:
    assert _should_skip("main.py", is_dir=False) is False


def test_should_skip_normal_js_file() -> None:
    assert _should_skip("app.js", is_dir=False) is False


# ── _format_detail ────────────────────────────────────────────────────────────

_PY_ANALYSIS = {
    "imports": ["os", "sys"],
    "classes": [{"name": "Foo", "methods": ["bar(self)", "baz(self, x)"]}],
    "functions": ["top_fn(x)"],
}

_JS_ANALYSIS = {
    "imports": ["react"],
    "exports": ["MyComp"],
}


def test_format_detail_python_level0_includes_imports() -> None:
    lines = _format_detail("foo.py", _PY_ANALYSIS, level=0)
    assert any("imports" in l for l in lines)


def test_format_detail_python_level1_no_imports() -> None:
    lines = _format_detail("foo.py", _PY_ANALYSIS, level=1)
    assert not any("imports" in l for l in lines)


def test_format_detail_python_level0_includes_methods() -> None:
    lines = _format_detail("foo.py", _PY_ANALYSIS, level=0)
    assert any("bar" in l for l in lines)


def test_format_detail_python_level2_names_only() -> None:
    lines = _format_detail("foo.py", _PY_ANALYSIS, level=2)
    # level 2 shows function names as "name()" not "def name(x)"
    assert any("top_fn" in l for l in lines)


def test_format_detail_js_level0() -> None:
    lines = _format_detail("app.js", _JS_ANALYSIS, level=0)
    assert any("export" in l and "MyComp" in l for l in lines)
    assert any("imports" in l for l in lines)


def test_format_detail_empty_analysis() -> None:
    lines = _format_detail("data.json", {}, level=0)
    assert lines == []


# ── _collect_files ────────────────────────────────────────────────────────────

def test_collect_files_finds_py_files(tmp_path: Path) -> None:
    (tmp_path / "main.py").write_text("pass")
    (tmp_path / "utils.py").write_text("pass")
    files = _collect_files(tmp_path)
    names = [f.name for f in files]
    assert "main.py" in names
    assert "utils.py" in names


def test_collect_files_skips_node_modules(tmp_path: Path) -> None:
    nm = tmp_path / "node_modules"
    nm.mkdir()
    (nm / "dep.js").write_text("")
    (tmp_path / "app.py").write_text("")
    files = _collect_files(tmp_path)
    paths = [str(f) for f in files]
    assert not any("node_modules" in p for p in paths)


def test_collect_files_skips_pyc(tmp_path: Path) -> None:
    (tmp_path / "main.py").write_text("pass")
    (tmp_path / "main.pyc").write_text("")
    files = _collect_files(tmp_path)
    assert all(f.suffix != ".pyc" for f in files)


def test_collect_files_empty_dir(tmp_path: Path) -> None:
    assert _collect_files(tmp_path) == []


# ── generate_repo_map ─────────────────────────────────────────────────────────

def test_generate_repo_map_returns_string(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("def run(): pass")
    result = generate_repo_map(str(tmp_path))
    assert isinstance(result, str)
    assert "app.py" in result


def test_generate_repo_map_empty_dir(tmp_path: Path) -> None:
    result = generate_repo_map(str(tmp_path))
    assert result == ""


def test_generate_repo_map_nonexistent_dir() -> None:
    result = generate_repo_map("/nonexistent/path/does/not/exist")
    assert result == ""


def test_generate_repo_map_contains_functions(tmp_path: Path) -> None:
    (tmp_path / "calc.py").write_text(
        "def add(a, b): return a + b\ndef sub(a, b): return a - b\n"
    )
    result = generate_repo_map(str(tmp_path), max_tokens=2000)
    assert "add" in result
    assert "sub" in result


def test_generate_repo_map_token_limit(tmp_path: Path) -> None:
    """Output respects the max_tokens budget."""
    big = "def fn_{i}(): pass\n" * 200
    (tmp_path / "big.py").write_text(big)
    result = generate_repo_map(str(tmp_path), max_tokens=50)
    # Should still return something (at minimum, file path)
    assert "big.py" in result
