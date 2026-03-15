"""Plan parser — extract structured file specs from a markdown plan.

Parses the structured plan format produced by Phase 2 into individual
file specifications, enabling file-by-file code generation.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


# ── Data structures ──────────────────────────────────────────────────────────

@dataclass
class FileSpec:
    """A single file's implementation specification."""
    path: str       # e.g. "src/auth.py"
    action: str     # "CREATE" or "MODIFY"
    spec: str       # The full spec text for this file


@dataclass
class StructuredPlan:
    """A parsed implementation plan with per-file specs."""
    shared_dependencies: str = ""       # The shared dependencies section
    files: list[FileSpec] = field(default_factory=list)
    raw: str = ""                       # Original full plan text (fallback)


# ── Regex patterns ───────────────────────────────────────────────────────────

# Match: ### path/to/file.py (CREATE) or ### path/to/file.py (MODIFY)
_FILE_HEADER = re.compile(
    r"^###\s+(.+?)\s*\((CREATE|MODIFY)\)\s*$",
    re.MULTILINE | re.IGNORECASE,
)

# Match: ## Shared Dependencies
_SHARED_DEPS = re.compile(
    r"^##\s+Shared\s+Dependenc(?:ies|y)",
    re.MULTILINE | re.IGNORECASE,
)

# Match: ## Files
_FILES_SECTION = re.compile(
    r"^##\s+Files?\s*$",
    re.MULTILINE | re.IGNORECASE,
)


# ── Parser ───────────────────────────────────────────────────────────────────

def parse_plan(plan_text: str) -> StructuredPlan:
    """Parse a structured plan into its components.

    Expects a plan with ``## Shared Dependencies`` and ``### file (ACTION)``
    sections.  If the plan doesn't follow the expected format, returns a
    ``StructuredPlan`` with an empty *files* list and the full text in *raw*,
    enabling fallback to monolithic implementation.
    """
    if not plan_text or not plan_text.strip():
        return StructuredPlan(raw=plan_text or "")

    result = StructuredPlan(raw=plan_text)

    # ── Extract shared dependencies section ──────────────────────────────
    deps_match = _SHARED_DEPS.search(plan_text)
    if deps_match:
        # Find the end of the section (next ## heading or end of text)
        start = deps_match.end()
        next_section = re.search(r"^##\s+", plan_text[start:], re.MULTILINE)
        end = start + next_section.start() if next_section else len(plan_text)
        result.shared_dependencies = plan_text[start:end].strip()

    # ── Extract per-file specs ───────────────────────────────────────────
    matches = list(_FILE_HEADER.finditer(plan_text))
    if not matches:
        return result  # unstructured plan — caller uses fallback

    for i, match in enumerate(matches):
        path = match.group(1).strip().strip("`")
        action = match.group(2).upper()

        # Body = everything from end of this header to start of next header
        body_start = match.end()
        if i + 1 < len(matches):
            body_end = matches[i + 1].start()
        else:
            # Last file — go to end, but stop at any ## heading
            remaining = plan_text[body_start:]
            next_h2 = re.search(r"^##\s+", remaining, re.MULTILINE)
            body_end = body_start + next_h2.start() if next_h2 else len(plan_text)

        spec = plan_text[body_start:body_end].strip()
        result.files.append(FileSpec(path=path, action=action, spec=spec))

    return result


def is_structured(plan: StructuredPlan | None) -> bool:
    """Return True if the plan was successfully parsed into file specs."""
    return plan is not None and len(plan.files) > 0
