# AI Orchestrator — Project Summary

## Project Overview

AI Orchestrator (`unicode` CLI) is a multi-agent automation pipeline that coordinates three AI coding agents — Claude Code, Codex CLI, and Qwen Coder — to complete software engineering tasks end-to-end. A user describes a task; the orchestrator runs it through five sequential phases (clarify → discuss → plan → implement → review), with built-in approval gates, iterative code review, persistent memory across sessions, and automatic git integration. The result is a tested, reviewed, committed implementation with no manual intervention required beyond the initial task description.

---

## Architecture

```
User Task
    │
    ▼
Phase 0: Clarify     — Claude asks 1-3 clarifying questions (optional, skipped in auto mode)
    │
    ▼
Phase 1: Discuss     — Claude + Codex discuss the approach (N rounds per tier)
    │                   Loop exits early once both agents signal agreement (AGREED vote)
    │
    ▼
Phase 2: Plan        — Codex writes the implementation plan from the agreed discussion
    │                   Plan written to .orchestrator/plan.md and shown for approval
    │                   (Claude does not re-review — agreement was reached in discussion)
    ▼
Phase 3: Implement   — Claude Code implements the plan against the working directory
    │
    ▼
Phase 4: Review      — Two-pass code review loop (up to N iterations per tier):
    │                     Part 1: Codex reviews the git diff → APPROVED or CHANGES_REQUESTED
    │                     Part 2: Claude validates Codex's findings → CONFIRMED or APPROVED
    │                     Developer (Claude): fixes all confirmed issues
    ▼
Finalization         — Commit, push (optional), update CLAUDE.md / AGENTS.md / orchestrator.md,
                        write to shared memory (YAML + markdown), update run history
```

**Key design principles:**
- Role separation: Qwen plans, Claude implements and reviews, Codex reviews and implements minor fixes
- Every approval gate is interactive (or auto-approved in `--auto` mode)
- Agents communicate via prompt construction — no direct API calls between agents
- Persistent state survives between sessions via `.orchestrator/` (all memory, notes, history, and YAML index live there)

---

## Folder Structure

```
ai-orchestrator/
├── orchestrator.py          Entry point. CLI definition (Click), phase orchestration,
│                            finalization, banner, session management, config loading.
├── config.yaml              Default config: models, timeouts, discussion rounds, tiers.
├── pyproject.toml           Package metadata. Installs as `unicode` CLI command.
├── requirements.txt         Runtime dependencies.
│
├── agents/                  Agent wrappers — thin adapters over external CLIs
│   ├── base.py              BaseAgent ABC: query(), implement(), review_query()
│   ├── claude_agent.py      Wraps `claude` CLI. Supports sdk/cli modes, streaming output.
│   ├── codex_agent.py       Wraps `codex` CLI. exec mode for implementation, text mode for review.
│   └── qwen_agent.py        Wraps `qwen` / Qwen Coder CLI for planning and synthesis.
│
├── phases/                  One module per pipeline phase
│   ├── clarify.py           Phase 0: Claude asks clarifying questions before planning.
│   ├── discuss.py           Phase 1: Multi-round Claude ↔ Codex discussion loop.
│   ├── plan.py              Phase 2: Qwen consolidates discussion into structured plan.
│   ├── implement.py         Phase 3: Claude implements the plan, handles timeout/cancel.
│   └── review.py            Phase 4: Two-pass review (Codex primary, Claude secondary).
│
├── utils/                   Shared utilities
│   ├── approval.py          Interactive approval prompts; auto-all session mode.
│   ├── git_utils.py         git add/diff/commit/push helpers. Windows-safe (junction points).
│   ├── history.py           Run history (.orchestrator/history.md), CLAUDE.md / AGENTS.md synthesis.
│   ├── logger.py            Structured terminal logging (Rich). Transcript file writer.
│   ├── init_project.py      /init command: scans codebase, Qwen fills all memory files.
│   ├── memory.py            Dual-write memory system (YAML index + markdown notes).
│   ├── runner.py            Subprocess runner with timeout, streaming, cancel support.
│   └── session.py           Session save/resume (.orchestrator/sessions/).
│
├── .orchestrator/           All auto-generated and persistent state (gitignored)
│   ├── memory.yaml          Machine-queryable memory index — injected into every prompt.
│   ├── history.md           Log of every completed run with task, outcome, duration.
│   ├── plan.md              Most recent implementation plan.
│   ├── bugs.md              Bug log: date, issue, root cause, solution, prevention.
│   ├── decisions.md         Architectural Decision Records (ADR-001, ADR-002, ...).
│   ├── key_facts.md         Project config, ports, URLs, conventions — filled by Qwen /init.
│   ├── issues.md            Work log: completed tasks with outcomes.
│   ├── sessions/            Saved session checkpoints (JSON).
│   └── transcript_*.log     Full conversation transcripts per run.
│
├── .agents/skills/          Universal agent skills directory (npx skills convention)
│   ├── find-skills/         Meta-skill: agents can search for and install new skills.
│   └── project-memory/      Memory skill: structured note-taking format reference.
│
├── .claude/skills/          Claude Code skills (symlinks → .agents/skills/)
├── .qwen/skills/            Qwen skills (symlinks → .agents/skills/)
│
├── CLAUDE.md                Claude Code context file — read on every startup.
└── AGENTS.md                Codex/generic agent context file — read on every startup.
```

---

## Tech Stack

| Layer | Technology |
|---|---|
| Language | Python 3.10+ |
| CLI framework | Click 8.x |
| Terminal UI | Rich 13.x |
| Config / Memory | PyYAML 6.x |
| Process mgmt | psutil 5.x |
| AI agents | Claude Code CLI, Codex CLI, Qwen Coder CLI |
| Skills | npx skills (Vercel Labs convention) |
| VCS | git (via subprocess) |

---

## Skills Ecosystem

The orchestrator integrates with the `npx skills` ecosystem so that agents can discover and apply skills autonomously.

### find-skills
- **Source:** `npx skills add https://github.com/vercel-labs/skills --skill find-skills`
- **Purpose:** Meta-skill — agents can run `npx skills find [query]` to search the registry and install new skills when they encounter a task that needs specialized knowledge.
- **Installed at:** `.agents/skills/find-skills`, `.claude/skills/find-skills`, `.qwen/skills/find-skills`
- **Global (Codex):** `~/.agents/skills/find-skills`, `~/.codex/skills/find-skills`

### project-memory
- **Source:** `npx skills add https://github.com/spillwavesolutions/project-memory --skill project-memory`
- **Purpose:** Defines the structured format for memory entries. All notes now live in `.orchestrator/` (bugs.md, decisions.md, key_facts.md, issues.md). Qwen populates them on `/init`; the orchestrator appends automatically at finalization.
- **Installed at:** `.agents/skills/project-memory`, `.claude/skills/project-memory`, `.qwen/skills/project-memory`
- **Global (Codex):** `~/.agents/skills/project-memory`, `~/.codex/skills/project-memory`
- **Orchestrator integration:** `utils/memory.py` implements all four note types (`log_bug`, `log_decision`, `log_issue`, `log_key_fact`) following the exact skill format. Notes are written automatically at finalization.

---

## Memory System

Two complementary stores run in parallel and are both written at the end of every task:

**YAML index** (`.orchestrator/memory.yaml`)
- Machine-queryable; injected into agent prompts via `get_context_for_task()`
- Categories: `patterns_learned`, `codebase_conventions`, `past_mistakes`, `architecture_decisions`, `task_index`
- Keyword-searched to find relevant past tasks and surface them in future prompts

**Markdown notes** (`.orchestrator/`) — same directory as the YAML index
- Human-readable; Qwen fills them on first run via `/init`, orchestrator appends on every task
- `.orchestrator/bugs.md` — structured bug log with root causes and prevention notes
- `.orchestrator/decisions.md` — ADRs with context, decision, alternatives, consequences
- `.orchestrator/key_facts.md` — project config, credentials, ports, important URLs
- `.orchestrator/issues.md` — work log with completed tasks and outcomes
- Also keyword-searched and excerpted into agent prompts

**Context injection:** `get_context_for_task(working_dir, task)` combines both stores and prepends the most relevant entries to every agent prompt, giving agents institutional knowledge from past runs.

---

## Task Complexity Tiers

Selected at task start via interactive prompt or `--tier quick|standard|complex`:

| Tier | Dev Model | Review Cycles | Discussion Rounds |
|---|---|---|---|
| quick | claude-sonnet | 1 | 1 |
| standard | claude-sonnet | 2 | 2 |
| complex | claude-opus | 3 | 4 |

---

## CLI Usage

```bash
# Run a task (interactive — prompts for task, tier, working dir)
unicode

# Run with explicit options
unicode --task "Add dark mode toggle" --working-dir ./myapp --tier standard

# Auto-approve all gates (non-interactive)
unicode --auto

# Resume a previous session
unicode --resume

# Force a specific model for the dev agent
unicode --dev-model opus

# Commit and push on completion
unicode --auto-commit --push
```

**In-session keyboard controls:**
- `Ctrl+C` — cancel current agent (skip to review phase)
- `Ctrl+Z` — cancel and skip to finalization
- Any approval prompt accepts: `y/yes`, `n/no`, `e/edit`, `s/skip`, `a/all` (approve all remaining)

---

## Good to Know

- **Working directory vs orchestrator directory:** The orchestrator repo and the project being worked on are separate. `--working-dir` points at the target project; the orchestrator's own `.orchestrator/` state is always written relative to the working directory.
- **Git integration:** At review phase, `git add -A` + `git diff --cached` captures all changes. Build artifacts and binary files are excluded from the review diff (but still staged). Windows junction-point permission warnings are treated as non-fatal.
- **CLAUDE.md / AGENTS.md synthesis:** At finalization, Codex rewrites both files by merging the existing body with new task knowledge. A hard 400-word cap is enforced in Python code regardless of model output. The persistent header (memory protocol section) is never overwritten.
- **Review ordering:** Codex → Claude secondary validation → Claude developer fix → repeat. The max-cycles bailout fires AFTER the fix phase, ensuring at least one fix round always runs.
- **Skills for Codex:** Codex uses `~/.codex/skills/` as its native skills directory (separate from `~/.agents/skills/`). Both are symlinked from the global install.
- **Config override:** All `config.yaml` values can be overridden via CLI flags. Tier settings override individual model/round settings.
