"""Repo skeleton map — compressed AST-like view of a codebase.

Generates a lightweight structural summary showing file paths, class names,
function signatures, and imports without reading file bodies.  Designed to
fit within ~2 000 tokens so agents can understand the project layout before
discussion or planning.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from utils.logger import log_info


# ── Ignore patterns ─────────────────────────────────────────────────────────

from utils.constants import IGNORE_DIRS as _IGNORE_DIRS, IGNORE_EXTS as _IGNORE_EXTS, IGNORE_FILES as _IGNORE_FILES

# ── Regex patterns for extraction ────────────────────────────────────────────

_PY_CLASS = re.compile(r"^(\s*)class\s+(\w+(?:\([^)]*\))?)\s*:", re.MULTILINE)
_PY_DEF = re.compile(r"^(\s*)def\s+(\w+\s*\([^)]*\))", re.MULTILINE)
_PY_IMPORT = re.compile(
    r"^(?:from\s+([\w.]+)\s+import|import\s+([\w.]+))", re.MULTILINE,
)

_JS_EXPORT = re.compile(
    r"^\s*export\s+(?:default\s+)?(?:function|class|const|let|var|interface|type|enum)\s+(\w+)",
    re.MULTILINE,
)
_JS_IMPORT = re.compile(r"""^\s*import\s+.*?from\s+['"]([^'"]+)['"]""", re.MULTILINE)


# ── File analysis ────────────────────────────────────────────────────────────

def _analyse_python(content: str) -> dict:
    """Extract classes, functions, and imports from Python source."""
    imports = sorted({
        (m.group(1) or m.group(2)).split(".")[0]
        for m in _PY_IMPORT.finditer(content)
    })

    classes: list[dict] = []
    functions: list[str] = []

    for m in _PY_CLASS.finditer(content):
        indent = len(m.group(1))
        cls_name = m.group(2)
        # Find methods belonging to this class (indented further)
        methods = []
        for dm in _PY_DEF.finditer(content[m.end():]):
            d_indent = len(dm.group(1))
            if d_indent <= indent:
                break
            methods.append(dm.group(2))
        classes.append({"name": cls_name, "methods": methods})

    for m in _PY_DEF.finditer(content):
        if len(m.group(1)) == 0:  # top-level only
            functions.append(m.group(2))

    return {"imports": imports, "classes": classes, "functions": functions}


def _analyse_js(content: str) -> dict:
    """Extract exports and import sources from JS/TS source."""
    imports = sorted({m.group(1) for m in _JS_IMPORT.finditer(content)})
    exports = [m.group(1) for m in _JS_EXPORT.finditer(content)]
    return {"imports": imports, "exports": exports}


# ── Tree builder ─────────────────────────────────────────────────────────────

def _should_skip(name: str, is_dir: bool) -> bool:
    """Check if a file or directory should be skipped."""
    if is_dir:
        return name in _IGNORE_DIRS or name.startswith(".")
    if name in _IGNORE_FILES:
        return True
    _, ext = os.path.splitext(name)
    return ext.lower() in _IGNORE_EXTS


def _collect_files(root: Path) -> list[Path]:
    """Walk the tree and return sorted file paths, respecting ignores."""
    files = []
    for dirpath, dirnames, filenames in os.walk(root):
        # Prune ignored directories in-place
        dirnames[:] = [
            d for d in sorted(dirnames)
            if not _should_skip(d, is_dir=True)
        ]
        for fname in sorted(filenames):
            if not _should_skip(fname, is_dir=False):
                files.append(Path(dirpath) / fname)
    return files


def _format_detail(
    rel_path: str,
    analysis: dict,
    level: int = 0,  # 0=full, 1=no imports, 2=names only
) -> list[str]:
    """Format analysis results for a single file."""
    lines: list[str] = []

    # Python file
    if "classes" in analysis and "functions" in analysis:
        if level == 0 and analysis.get("imports"):
            lines.append(f"    imports: {', '.join(analysis['imports'])}")
        for cls in analysis["classes"]:
            lines.append(f"    class {cls['name']}:")
            if level < 2:
                for meth in cls["methods"]:
                    lines.append(f"      def {meth}")
        for fn in analysis["functions"]:
            if level < 2:
                lines.append(f"    def {fn}")
            else:
                lines.append(f"    {fn}()")

    # JS/TS file
    elif "exports" in analysis:
        if level == 0 and analysis.get("imports"):
            lines.append(f"    imports: {', '.join(analysis['imports'])}")
        for exp in analysis["exports"]:
            lines.append(f"    export {exp}")

    return lines


# ── Public API ───────────────────────────────────────────────────────────────

def generate_repo_map(working_dir: str, max_tokens: int = 2000) -> str:
    """Generate a compressed skeleton map of the codebase.

    Walks the project tree and extracts structural information (classes,
    functions, imports) from Python and JS/TS files.  Other files are listed
    by path only.

    The output is progressively truncated to fit within *max_tokens*
    (estimated at 4 chars per token):
      1. Full detail (imports + signatures)
      2. Drop imports
      3. Drop method/function details, keep only names
      4. File tree only (no analysis)

    Returns a formatted string suitable for injection into agent prompts,
    or ``""`` if the directory is empty or unreadable.
    """
    root = Path(working_dir).resolve()
    if not root.is_dir():
        return ""

    files = _collect_files(root)
    if not files:
        return ""

    # Analyse each file
    analyses: dict[str, dict] = {}
    for fpath in files:
        rel = str(fpath.relative_to(root)).replace("\\", "/")
        ext = fpath.suffix.lower()
        if ext == ".py":
            try:
                content = fpath.read_text(encoding="utf-8", errors="ignore")
                analyses[rel] = _analyse_python(content)
            except OSError:
                analyses[rel] = {}
        elif ext in {".js", ".ts", ".jsx", ".tsx"}:
            try:
                content = fpath.read_text(encoding="utf-8", errors="ignore")
                analyses[rel] = _analyse_js(content)
            except OSError:
                analyses[rel] = {}
        else:
            analyses[rel] = {}

    max_chars = max_tokens * 4

    # Try progressively less detailed output
    for level in range(4):
        output = _build_output(root, analyses, level)
        if len(output) <= max_chars:
            log_info(f"Repo map: {len(files)} files, ~{len(output) // 4} tokens (detail level {level})")
            return output

    # Final fallback — just file count
    return f"PROJECT STRUCTURE: {len(files)} files (too large for detailed map)\n"


def _build_output(root: Path, analyses: dict[str, dict], level: int) -> str:
    """Build the map string at the given detail level.

    Levels: 0=full, 1=no imports, 2=names only, 3=paths only
    """
    lines = ["PROJECT STRUCTURE:"]
    prev_parts: list[str] = []

    for rel_path in sorted(analyses.keys()):
        parts = rel_path.split("/")

        # Print directory headers when the path prefix changes
        for i, part in enumerate(parts[:-1]):
            if i >= len(prev_parts) or prev_parts[i] != part:
                indent = "  " * (i + 1)
                lines.append(f"{indent}{part}/")

        # Print filename
        indent = "  " * len(parts)
        lines.append(f"{indent}{parts[-1]}")

        # Print details (unless level 3 = paths only)
        if level < 3:
            analysis = analyses[rel_path]
            if analysis:
                detail = _format_detail(rel_path, analysis, level)
                lines.extend(detail)

        prev_parts = parts

    return "\n".join(lines) + "\n"
