"""Project initialization — scan an existing codebase and bootstrap memory files.

Called by the ``/init`` slash command.  Works on any project that:
  - has no ``.orchestrator/`` memory files yet (brand-new integration)
  - has old ``CLAUDE.md`` / ``AGENTS.md`` files lacking the memory-protocol header
  - has empty memory files (only the auto-generated header line)

Uses Claude haiku to analyse the codebase and fill every store with real,
project-specific knowledge so agents are useful from the very first task.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn

from utils.logger import log_error, log_info, log_success
from utils.memory import (
    init_project_notes,
    load_memory,
    log_decision,
    log_key_fact,
    save_memory,
)

console = Console()

# ── File-scanning constants ───────────────────────────────────────────────────

from utils.constants import IGNORE_DIRS as _SKIP_DIRS, IGNORE_EXTS as _SKIP_EXTENSIONS

# Files to read for project context (checked in order, root-relative)
_KEY_FILES: list[str] = [
    "README.md", "README.rst", "README.txt", "README",
    "package.json", "pyproject.toml", "setup.py", "setup.cfg",
    "requirements.txt", "requirements-dev.txt", "requirements-test.txt",
    "Cargo.toml", "go.mod", "pom.xml", "build.gradle",
    "docker-compose.yml", "docker-compose.yaml", "Dockerfile",
    ".env.example", "config.yaml", "config.yml", "config.json",
    "Makefile", "justfile",
]

_MAX_TREE_ENTRIES = 100   # lines in the file tree before truncation
_MAX_PER_FILE = 1_800     # chars per key file
_MAX_KEY_FILES_TOTAL = 6_000  # total chars from key files
_MAX_SOURCE_SAMPLE = 3_000    # total chars from source samples
_MAX_SOURCE_FILES = 5         # how many source files to sample

# ── Source-code extensions worth reading ─────────────────────────────────────

_SOURCE_EXTS: frozenset[str] = frozenset({
    ".py", ".js", ".ts", ".jsx", ".tsx",
    ".go", ".rs", ".java", ".rb", ".php",
    ".c", ".cpp", ".h", ".hpp",
    ".cs", ".swift", ".kt",
})

# Entry-point stem names get priority when sampling source files
_ENTRY_STEMS: frozenset[str] = frozenset({
    "main", "app", "index", "server", "cli", "__main__",
    "manage", "wsgi", "asgi", "run",
})


# ── Project-scanning helpers ──────────────────────────────────────────────────

def _build_file_tree(work_dir: str) -> str:
    """Return a filtered file-tree string (at most _MAX_TREE_ENTRIES lines)."""
    root = Path(work_dir)
    lines: list[str] = []
    count = 0

    for dirpath_str, dirnames, filenames in os.walk(root):
        dirpath = Path(dirpath_str)
        # Prune skip dirs in-place so os.walk doesn't recurse into them
        dirnames[:] = sorted(
            d for d in dirnames
            if d not in _SKIP_DIRS and not d.endswith(".egg-info")
        )
        rel = dirpath.relative_to(root)
        depth = len(rel.parts)

        if depth > 0:
            indent = "  " * (depth - 1)
            lines.append(f"{indent}{rel.parts[-1]}/")

        file_indent = "  " * depth
        for fname in sorted(filenames):
            if Path(fname).suffix.lower() in _SKIP_EXTENSIONS:
                continue
            lines.append(f"{file_indent}{fname}")
            count += 1
            if count >= _MAX_TREE_ENTRIES:
                lines.append(f"  ... ({count}+ files, truncated)")
                return "\n".join(lines)

    return "\n".join(lines)


def _read_key_files(work_dir: str) -> str:
    """Read well-known project config/meta files and return their contents."""
    root = Path(work_dir)
    parts: list[str] = []
    total = 0

    for rel in _KEY_FILES:
        path = root / rel
        if not path.exists():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
            snip = text[:_MAX_PER_FILE]
            if len(text) > _MAX_PER_FILE:
                snip += f"\n... (truncated at {_MAX_PER_FILE} chars)"
            parts.append(f"=== {rel} ===\n{snip}")
            total += len(snip)
            if total >= _MAX_KEY_FILES_TOTAL:
                break
        except OSError:
            continue

    return "\n\n".join(parts) if parts else "(no standard config/meta files found)"


def _read_source_samples(work_dir: str) -> str:
    """Sample the most likely entry-point source files for architecture context."""
    root = Path(work_dir)
    candidates: list[tuple[int, int, Path]] = []  # (priority, depth, path)

    for dirpath_str, dirnames, filenames in os.walk(root):
        dirpath = Path(dirpath_str)
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        rel = dirpath.relative_to(root)
        depth = len(rel.parts)
        if depth > 4:
            continue
        for fname in filenames:
            p = Path(fname)
            if p.suffix.lower() not in _SOURCE_EXTS:
                continue
            priority = 0
            if p.stem.lower() in _ENTRY_STEMS:
                priority += 10
            if depth == 0:
                priority += 5
            elif depth == 1:
                priority += 2
            candidates.append((priority, depth, dirpath / fname))

    candidates.sort(key=lambda x: (-x[0], x[1]))

    parts: list[str] = []
    total = 0
    for _, _, fpath in candidates[:_MAX_SOURCE_FILES]:
        try:
            text = fpath.read_text(encoding="utf-8", errors="replace")
            snip = text[:(_MAX_SOURCE_SAMPLE // _MAX_SOURCE_FILES)]
            if len(text) > len(snip):
                snip += "\n... (truncated)"
            rel_label = fpath.relative_to(root)
            parts.append(f"=== {rel_label} ===\n{snip}")
            total += len(snip)
            if total >= _MAX_SOURCE_SAMPLE:
                break
        except OSError:
            continue

    return "\n\n".join(parts) if parts else "(no source files found)"


# ── Content-presence checks ───────────────────────────────────────────────────

def _has_real_content(work_dir: str, filename: str) -> bool:
    """Return True if a notes file has entries beyond the auto-generated header.

    The auto-generated header is 4 non-blank lines.  Any file with more than
    that has real content and should not be overwritten.
    """
    path = Path(work_dir) / ".orchestrator" / filename
    if not path.exists():
        return False
    try:
        text = path.read_text(encoding="utf-8")
        meaningful = [l for l in text.splitlines() if l.strip()]
        return len(meaningful) > 4
    except OSError:
        return False


def _needs_memory_upgrade(work_dir: str, filename: str) -> bool:
    """Return True if CLAUDE.md/AGENTS.md exists but lacks the memory-protocol section."""
    path = Path(work_dir) / filename
    if not path.exists():
        return False
    try:
        text = path.read_text(encoding="utf-8")
        return ".orchestrator/" not in text
    except OSError:
        return False


# ── Claude haiku analysis prompt ─────────────────────────────────────────────

_ANALYSIS_PROMPT = """\
You are analyzing a software project to bootstrap AI agent memory files.
Your output seeds institutional knowledge so agents work effectively from day one.

PROJECT FILE TREE:
{tree}

KEY PROJECT FILES (README, package files, config):
{key_files}

MAIN SOURCE FILE SAMPLES:
{source}

Analyze this project and return a single JSON object with EXACTLY this structure
(no extra keys, no markdown fences, just raw JSON):

{{
  "project_name": "short name for this project",
  "description": "1-2 sentences describing what this project does",
  "tech_stack": ["list each technology, framework, language, and key library"],
  "entry_points": ["how to run this project, e.g. 'python main.py' or 'npm start'"],
  "key_facts": [
    {{"category": "Ports & URLs",  "fact": "specific port or URL if found"}},
    {{"category": "Config",        "fact": "where config is stored and key env vars"}},
    {{"category": "Database",      "fact": "database engine and connection info if visible"}},
    {{"category": "Auth",          "fact": "authentication mechanism if present"}},
    {{"category": "Testing",       "fact": "test framework and how to run tests"}},
    {{"category": "Deployment",    "fact": "how the project is deployed or packaged"}}
  ],
  "conventions": [
    "a specific naming or coding convention observed in the source",
    "a structural pattern (e.g. all routes in routes/, models in models/)",
    "an error-handling pattern",
    "import style or module organization rule"
  ],
  "architectural_decisions": [
    {{
      "title": "concise decision title",
      "context": "why this decision was needed (1-2 sentences)",
      "decision": "what was decided and how it is implemented",
      "consequences": "what this means for future development"
    }}
  ],
  "past_mistakes_to_avoid": [
    "a concrete gotcha or anti-pattern an AI agent would likely make in this codebase",
    "a second one"
  ]
}}

Rules:
- Use ONLY facts visible in the files — do not invent anything.
- Omit any key_facts entry whose fact you cannot determine from the files.
- architectural_decisions: aim for 2–4 entries on real design choices, not trivial ones.
- conventions: aim for 3–5 specific, actionable patterns actually seen in the code.
- past_mistakes_to_avoid: 2–3 things an AI agent unfamiliar with this codebase would get wrong.
- Return ONLY valid JSON. No preamble, no explanation, no markdown code fences.
"""


# ── orchestrator.md prompt ────────────────────────────────────────────────────

_ORCH_MD_PROMPT = """\
Write an orchestrator.md file for this software project.
This file is read by both humans and AI agents to understand the project quickly.

PROJECT NAME: {name}
DESCRIPTION: {description}
TECH STACK: {tech}
ENTRY POINTS: {entry}

FILE TREE:
{tree}

KEY FILES:
{key_files}

Write clean Markdown with these sections:
# <Project Name> — Project Summary

## Project Overview
(1 paragraph — what it does, who uses it, main value)

## Architecture
(how the code is organized, key patterns and layers)

## Folder Structure
(table or bullet list: path → what it contains)

## Tech Stack
(table: Layer | Technology)

## Good to Know
(3–6 bullet points: quirks, conventions, gotchas, how to run it locally)

Be concise but thorough.  Max ~600 words.
"""


# ── Memory-protocol header injection ─────────────────────────────────────────

from utils.history import _MEMORY_PROTOCOL_SECTION  # noqa: E402 — shared constant


def _upgrade_agent_md(work_dir: str, filename: str) -> bool:
    """Append the memory-protocol section to an existing agent MD that lacks it.

    Returns True if the file was upgraded, False if no action was needed.
    """
    path = Path(work_dir) / filename
    if not path.exists():
        return False
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return False

    if ".orchestrator/" in text:
        return False  # already has it

    path.write_text(text.rstrip() + "\n\n" + _MEMORY_PROTOCOL_SECTION, encoding="utf-8")
    return True


# ── Main init entry point ─────────────────────────────────────────────────────

def run_init(work_dir: str, agent) -> None:
    """Scan *work_dir* and populate all memory files using *agent* (Claude haiku).

    Idempotent: files that already have real content are left untouched.
    Old CLAUDE.md / AGENTS.md files missing the memory-protocol section are
    upgraded in-place (content preserved, header appended).
    """
    today = datetime.now().strftime("%Y-%m-%d")
    root = Path(work_dir)

    console.print()
    console.print(Panel(
        f"[bold]Scanning[/] [cyan]{work_dir}[/]\n"
        "[dim]Claude (haiku) will analyse the codebase and fill all memory files.[/]",
        title="[bold cyan]Project Init[/]",
        border_style="cyan",
        padding=(0, 2),
    ))
    console.print()

    # ── Step 1: Ensure memory directory and files exist ───────────────────────
    init_project_notes(work_dir)

    # ── Step 2: Audit current state ───────────────────────────────────────────
    already: dict[str, bool] = {
        f: _has_real_content(work_dir, f)
        for f in ("bugs.md", "decisions.md", "key_facts.md", "issues.md")
    }
    upgrade_claude = _needs_memory_upgrade(work_dir, "CLAUDE.md")
    upgrade_agents = _needs_memory_upgrade(work_dir, "AGENTS.md")
    has_orch_md    = (root / "orchestrator.md").exists()

    skipping = [f for f, v in already.items() if v]
    if skipping:
        console.print(f"[dim]  Skipping (already populated): {', '.join(skipping)}[/]")

    # ── Step 3: Gather context ────────────────────────────────────────────────
    with Progress(
        SpinnerColumn(),
        TextColumn("[dim]{task.description}"),
        console=console,
        transient=True,
    ) as progress:
        t = progress.add_task("Building file tree …", total=None)
        tree = _build_file_tree(work_dir)

        progress.update(t, description="Reading project files …")
        key_files = _read_key_files(work_dir)

        progress.update(t, description="Sampling source files …")
        source = _read_source_samples(work_dir)

        # ── Step 4: Analyse with Claude haiku ────────────────────────────────
        progress.update(t, description="Claude (haiku) is analysing the project …")
        prompt = _ANALYSIS_PROMPT.format(
            tree=tree[:3_000],
            key_files=key_files[:5_000],
            source=source[:2_500],
        )
        try:
            raw = agent.query(prompt)
        except Exception as exc:
            log_error(f"Analysis failed: {exc}")
            return

        # Strip accidental markdown fences
        text = raw.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()

        try:
            analysis: dict = json.loads(text)
        except (json.JSONDecodeError, ValueError) as exc:
            log_error(f"Could not parse analysis JSON ({exc}) — raw output:")
            console.print(f"[dim red]{raw[:400]}[/]")
            return

        progress.update(t, description="Writing memory files …")

    # ── Step 5: Populate key_facts.md ────────────────────────────────────────
    facts_written = 0
    if not already["key_facts.md"]:
        desc = analysis.get("description", "")
        if desc:
            log_key_fact(work_dir, "Project Overview", desc, today)
            facts_written += 1

        tech = analysis.get("tech_stack", [])
        if tech:
            log_key_fact(work_dir, "Tech Stack", ", ".join(tech), today)
            facts_written += 1

        entry = analysis.get("entry_points", [])
        if entry:
            log_key_fact(work_dir, "Entry Points", " | ".join(entry), today)
            facts_written += 1

        for item in analysis.get("key_facts", []):
            cat  = item.get("category", "General")
            fact = item.get("fact", "").strip()
            if fact:
                log_key_fact(work_dir, cat, fact, today)
                facts_written += 1

    # ── Step 6: Populate decisions.md ────────────────────────────────────────
    adrs_written = 0
    if not already["decisions.md"]:
        for adr in analysis.get("architectural_decisions", []):
            title        = adr.get("title", "").strip()
            context      = adr.get("context", "").strip()
            decision     = adr.get("decision", "").strip()
            consequences = adr.get("consequences", "").strip()
            if title and decision:
                log_decision(work_dir, title, context, decision,
                             consequences=consequences, date=today)
                adrs_written += 1

    # ── Step 7: Update YAML memory store ─────────────────────────────────────
    memory = load_memory(work_dir)

    convs_written = 0
    for conv in analysis.get("conventions", []):
        if conv.strip():
            memory["codebase_conventions"].append({"date": today, "text": conv.strip()})
            convs_written += 1

    mistakes_written = 0
    for m in analysis.get("past_mistakes_to_avoid", []):
        if m.strip():
            memory["past_mistakes"].append({"date": today, "text": m.strip()})
            mistakes_written += 1

    for adr in analysis.get("architectural_decisions", []):
        title    = adr.get("title", "")
        decision = adr.get("decision", "")
        if title and decision:
            memory["architecture_decisions"].append({
                "date": today,
                "text": f"{title}: {decision[:150]}",
            })

    save_memory(work_dir, memory)

    # ── Step 8: Upgrade old CLAUDE.md / AGENTS.md ────────────────────────────
    upgraded: list[str] = []
    if upgrade_claude and _upgrade_agent_md(work_dir, "CLAUDE.md"):
        upgraded.append("CLAUDE.md")
    if upgrade_agents and _upgrade_agent_md(work_dir, "AGENTS.md"):
        upgraded.append("AGENTS.md")

    # ── Step 9: Generate orchestrator.md if missing ───────────────────────────
    orch_generated = False
    if not has_orch_md:
        orch_prompt = _ORCH_MD_PROMPT.format(
            name=analysis.get("project_name", root.name),
            description=analysis.get("description", ""),
            tech=", ".join(analysis.get("tech_stack", [])),
            entry=" | ".join(analysis.get("entry_points", [])),
            tree=tree[:2_000],
            key_files=key_files[:2_500],
        )
        try:
            orch_md_content = agent.query(orch_prompt)
            if orch_md_content.strip():
                (root / "orchestrator.md").write_text(
                    orch_md_content.strip() + "\n", encoding="utf-8"
                )
                orch_generated = True
        except Exception as exc:
            log_error(f"orchestrator.md generation failed: {exc}")

    # ── Step 10: Summary ──────────────────────────────────────────────────────
    console.print()
    name = analysis.get("project_name", root.name)
    desc = analysis.get("description", "")
    console.print(Panel(
        f"[bold white]{name}[/]\n[dim]{desc}[/]",
        title="[bold green]Project Init Complete[/]",
        border_style="green",
        padding=(0, 2),
    ))
    console.print()

    def _row(label: str, value: str, skipped: bool = False) -> None:
        icon = "[dim]–[/]" if skipped else "[bold green]✓[/]"
        note = " [dim](already had content — skipped)[/]" if skipped else ""
        console.print(f"  {icon}  [dim]{label:<35}[/] {value}{note}")

    _row(".orchestrator/key_facts.md",
         f"[cyan]{facts_written} facts written[/]", already["key_facts.md"])
    _row(".orchestrator/decisions.md",
         f"[cyan]{adrs_written} ADRs written[/]",   already["decisions.md"])
    _row(".orchestrator/memory.yaml",
         f"[cyan]{convs_written} conventions · {mistakes_written} pitfalls[/]")

    if upgraded:
        for f in upgraded:
            _row(f, "[yellow]memory-protocol header added[/]")
    if orch_generated:
        _row("orchestrator.md", "[cyan]generated[/]")
    elif has_orch_md:
        _row("orchestrator.md", "", skipped=True)

    console.print()
    console.print(
        "  [dim]Run [bold white]/init[/] again any time — it will only fill empty files.[/]"
    )
    console.print()


