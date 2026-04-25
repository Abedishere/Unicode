"""Phase 0.5b: Skills Scout — dedicated Kiro agent that finds skills for every pipeline role.

Runs in parallel with Phase 0.5 (Research) inside _run_task.

Workflow:
  1. Kiro Scout generates 2 search queries per pipeline role
     (researcher · planner · developer · reviewer).
  2. For each role, runs `npx skills find` for each query.
  3. Installs the top matching skills globally (-g -y).
  4. Reads each installed skill's SKILL.md.
  5. Returns a SkillsManifest with per-role skill guidance.

Developer gets 3 skills (most hands-on); all other roles get 2.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from agents.base import BaseAgent
from utils.logger import log_agent, log_info, log_phase
from utils.skills_discovery import discover_and_install, search_skills

# How many skills to install per role
_SKILLS_PER_ROLE: dict[str, int] = {
    "researcher": 2,
    "planner":    2,
    "developer":  3,  # most hands-on — gets one extra skill
    "reviewer":   2,
}

_SCOUT_PROMPT = """\
<role>You are a Skills Scout. Your only job is to generate search queries \
to find relevant skills from the skills.sh registry for each role in a \
software pipeline.</role>

<task>{task}</task>

The pipeline has four roles with DISTINCT needs — do NOT overlap queries between roles:
- researcher: finds existing solutions, libraries, APIs, and competitive products
- planner: designs architecture, selects tech stack, writes the implementation plan
- developer: writes the actual code — needs IMPLEMENTATION skills \
(language patterns, framework templates, debugging, code style, DX tooling)
- reviewer: audits finished code — needs REVIEW/AUDIT skills \
(static analysis, linting, security scanning, performance profiling, accessibility, test coverage)

IMPORTANT: developer queries must target code-writing tools; \
reviewer queries must target code-auditing tools. They serve opposite ends of the same task.

Generate 2 focused search queries per role that are specific to the task above.

Return ONLY valid JSON — no markdown fences, no explanation:
{{
  "researcher": ["query1", "query2"],
  "planner":    ["query1", "query2"],
  "developer":  ["query1", "query2"],
  "reviewer":   ["query1", "query2"]
}}
"""


@dataclass
class SkillsManifest:
    """Per-role skill guidance blocks ready to inject into agent prompts."""

    researcher: str = ""
    planner: str = ""
    developer: str = ""
    reviewer: str = ""

    def is_empty(self) -> bool:
        return not any((self.researcher, self.planner, self.developer, self.reviewer))

    def format_for_role(self, role: str) -> str:
        """Return a `<skills>` XML block for *role*, or empty string if none."""
        content = getattr(self, role, "")
        if not content:
            return ""
        return f"<skills>\n{content}\n</skills>\n\n"


def _format_skills_block(skills: dict[str, str]) -> str:
    """Format {package: content} into a readable multi-skill block."""
    if not skills:
        return ""
    parts = []
    for pkg, content in skills.items():
        skill_name = pkg.split("@")[-1] if "@" in pkg else pkg
        parts.append(f"### {skill_name}\n{content.strip()}")
    return "\n\n---\n\n".join(parts)


def run_skills_scout(task: str, kiro: BaseAgent) -> SkillsManifest:
    """Run the Skills Scout and return a populated SkillsManifest.

    Non-fatal: any failure at any step returns whatever was collected so far
    (or an empty manifest if Kiro's query itself fails).
    """
    log_phase("Phase 0.5b: Skills Scout")
    log_info("Kiro (Skills Scout) generating role-specific skill queries …")

    # ── Step 1: Ask Kiro for role-specific search queries ─────────────────────
    try:
        raw = kiro.query(_SCOUT_PROMPT.format(task=task[:600]))
    except Exception as exc:
        log_info(f"Skills Scout query failed (non-fatal): {exc}")
        return SkillsManifest()

    # Strip markdown fences if Kiro wrapped its JSON
    cleaned = re.sub(r"```(?:json)?\s*|\s*```", "", raw).strip()
    try:
        queries: dict[str, list[str]] = json.loads(cleaned)
    except Exception:
        log_info("Skills Scout could not parse Kiro response — skipping.")
        return SkillsManifest()

    log_agent("Skills Scout (Kiro)", raw)

    # ── Step 2: Search, install, and read for each role ───────────────────────
    manifest = SkillsManifest()
    roles = _SKILLS_PER_ROLE.keys()  # researcher, planner, developer, reviewer

    # Track every package already assigned to an earlier role so the same skill
    # is never recommended to two different roles.
    globally_assigned: set[str] = set()

    for role in roles:
        role_queries = queries.get(role, [])
        if not role_queries:
            continue

        # Collect unique packages across both queries for this role,
        # excluding anything already given to a previous role.
        # Fetch a fixed pool per query and post-filter — avoids inflating top_n
        # as globally_assigned grows across roles.
        seen: list[str] = []
        for query in role_queries[:2]:
            for pkg in search_skills(query, top_n=8):
                if pkg not in seen and pkg not in globally_assigned:
                    seen.append(pkg)

        if not seen:
            log_info(f"Skills Scout ({role}): no results found.")
            continue

        # Take top N for this role
        top_packages = seen[:_SKILLS_PER_ROLE[role]]
        log_info(f"Skills Scout ({role}): installing {top_packages} …")

        globally_assigned.update(top_packages)
        installed = discover_and_install(top_packages)
        if installed:
            block = _format_skills_block(installed)
            setattr(manifest, role, block)
            log_info(
                f"Skills Scout ({role}): "
                f"{len(installed)} skill(s) ready — "
                f"{[p.split('@')[-1] for p in installed]}"
            )
        else:
            log_info(f"Skills Scout ({role}): installed but no SKILL.md found.")

    # ── Step 3: Summary ───────────────────────────────────────────────────────
    active = [r for r in roles if getattr(manifest, r)]
    if active:
        log_info(f"Skills Scout complete — skills ready for: {active}")
    else:
        log_info("Skills Scout complete — no skills found (non-fatal).")

    return manifest
