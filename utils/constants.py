"""Shared constants for directory/file ignore patterns."""

from __future__ import annotations

# Directories to skip when walking the project tree.
# Used by repo_map, init_project, and git_utils (diff exclusion).
IGNORE_DIRS: frozenset[str] = frozenset({
    # VCS / IDE
    ".git", ".idea", ".vscode",
    # Python
    ".venv", "venv", "env", "__pycache__", ".tox", ".pytest_cache",
    ".mypy_cache", ".eggs", "site-packages", "htmlcov", "egg-info",
    # Node / JS
    "node_modules", ".next", ".nuxt",
    # Build outputs
    "build", "dist", "out", "target", "vendor",
    # Java / Kotlin / Android / Gradle
    ".gradle",
    # Dart / Flutter
    ".dart_tool", ".flutter-plugins", ".flutter-plugins-dependencies",
    # Swift / iOS
    "Pods", ".build",
    # Rust
    ".cargo",
    # Coverage / CI
    "coverage", ".nyc_output",
    # Project-specific
    ".orchestrator",
})

# Binary / compiled / non-reviewable file extensions.
IGNORE_EXTS: frozenset[str] = frozenset({
    # Compiled
    ".pyc", ".pyo", ".pyd", ".class", ".o", ".so", ".dll", ".exe", ".wasm",
    ".jar", ".war", ".ear", ".aar",
    # Images
    ".png", ".jpg", ".jpeg", ".gif", ".ico", ".svg", ".webp", ".bmp",
    # Fonts
    ".woff", ".woff2", ".ttf",
    # Archives
    ".zip", ".tar", ".gz", ".bz2", ".xz", ".whl",
    # Minified / maps
    ".min.js", ".min.css", ".map",
    # Documents
    ".pdf",
    # Lock files
    ".lock",
})

# Lock / generated files to skip when scanning for project context.
IGNORE_FILES: frozenset[str] = frozenset({
    "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock",
})
