"""Skills discovery utilities — thin wrapper around `npx skills find` / `npx skills add`.

Used by the Skills Scout (phases/skills_scout.py) to search skills.sh,
install skills globally, and read their SKILL.md content.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from utils.logger import log_info

import sys

# On Windows, npx is a .cmd file and needs shell=True or explicit .cmd suffix
_NPX = "npx.cmd" if sys.platform == "win32" else "npx"

# All locations where globally installed skills land
_GLOBAL_SKILLS_DIRS: list[Path] = [
    Path.home() / ".agents" / "skills",
    Path.home() / ".claude" / "skills",
    Path.home() / ".codex" / "skills",
    Path.home() / ".qwen" / "skills",
]

# Cap individual SKILL.md content to keep prompts reasonable
_SKILL_CONTENT_CAP = 2000


def search_skills(query: str, top_n: int = 3, timeout: int = 25) -> list[str]:
    """Run `npx skills find <query>` and return the top *top_n* package names.

    Returns empty list on any failure (non-fatal).
    """
    try:
        result = subprocess.run(
            [_NPX, "skills", "find", query],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            input="\n",
        )
        output = (result.stdout or "") + (result.stderr or "")
        # Strip ANSI escape sequences before parsing
        output = re.sub(r"\x1b\[[0-9;]*m", "", output)
        # Lines look like: "owner/repo@skill-name  342 installs"
        # Require owner to start with a letter to exclude the banner "owner/repo@skill" placeholder
        packages = re.findall(
            r"\b([a-zA-Z][a-zA-Z0-9_.-]*/[a-zA-Z0-9_.-]+@[a-zA-Z0-9_.-]+)\b", output
        )
        # Deduplicate while preserving order
        seen: list[str] = []
        for p in packages:
            if p not in seen and p != "owner/repo@skill":
                seen.append(p)
        return seen[:top_n]
    except Exception as exc:
        log_info(f"skills find '{query}' failed (non-fatal): {exc}")
        return []


def install_skill(package: str, timeout: int = 60) -> bool:
    """Run `npx skills add <package> -g -y` to install globally.

    Returns True on success, False on any failure (non-fatal).
    """
    try:
        result = subprocess.run(
            [_NPX, "skills", "add", package, "-g", "-y"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            input="\n",
        )
        return result.returncode == 0
    except Exception as exc:
        log_info(f"skill install '{package}' failed (non-fatal): {exc}")
        return False


def read_skill_content(package: str) -> str:
    """Read the installed SKILL.md for *package*.

    Extracts the skill directory name from the package string
    (e.g. "owner/repo@skill-name" → "skill-name") and checks all
    known global skills directories.

    Returns empty string if not found.
    """
    skill_name = package.split("@")[-1] if "@" in package else package
    for base_dir in _GLOBAL_SKILLS_DIRS:
        skill_md = base_dir / skill_name / "SKILL.md"
        if skill_md.exists():
            try:
                return skill_md.read_text(encoding="utf-8")[:_SKILL_CONTENT_CAP]
            except Exception:
                pass
    return ""


def discover_and_install(packages: list[str]) -> dict[str, str]:
    """Install each package (if not already installed) and return {package: content}.

    Only packages whose SKILL.md can be read are included in the result.
    """
    result: dict[str, str] = {}
    for pkg in packages:
        # Check if already installed before attempting install
        content = read_skill_content(pkg)
        if not content:
            if install_skill(pkg):
                content = read_skill_content(pkg)
        if content:
            result[pkg] = content
    return result
