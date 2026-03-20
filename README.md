<p align="center">
  <img src="assets/unicode-logo.png" alt="Unicode" width="900"/>
</p>

# Unicode — AI Agent Orchestrator

Multi-agent orchestrator that coordinates **Claude Code**, **Codex CLI**, and **Qwen CLI** to collaboratively plan, implement, and review code — with persistent memory, structured project notes, and an extensible skills ecosystem.

## How it works

```
Phase 0: Clarify      — Claude asks clarifying questions (skipped in auto mode)
Phase 1: Discuss      — Claude + Codex debate the approach (N rounds per tier)
                          · Repo skeleton map injected for structural context
                          · Sliding window keeps only last 2 exchanges verbatim;
                            older rounds compressed to ~150-char summaries
Phase 2: Plan         — Qwen synthesizes discussion into a structured plan
                          · Outputs per-file specs (CREATE|MODIFY) with shared
                            dependencies section
Phase 3: Implement    — Claude implements the plan with full file access
                          · File-by-file generation when plan is structured
                            (each file gets focused prompt with skeleton + its spec)
                          · Falls back to monolithic implementation otherwise
Phase 4: Review       — Tiered diff review loop:
                          · Codex receives structured diff summary (files changed,
                            functions added/modified/removed) instead of raw diff
                          · Codex responds APPROVED, CHANGES_REQUESTED, or
                            NEED_FULL_DIFF: <filename> to escalate specific files
                          · Claude validates Codex's findings → CONFIRMED or APPROVED
                          · Claude (developer) fixes all confirmed issues
                          · Repeats until approved or max cycles reached
Finalization          — Commit, update context files, write to shared memory
```

**Agent roles:**
- **Claude** — discussion, implementation, secondary review, context file synthesis
- **Codex** — discussion, primary code review, minor fixes
- **Qwen** — plan synthesis, `orchestrator.md` documentation

## Prerequisites

You need these CLI tools installed and available on your PATH:

| Tool | Install |
|---|---|
| **Claude Code** | `npm install -g @anthropic-ai/claude-code` |
| **Codex CLI** | `npm install -g @openai/codex` |
| **Qwen CLI** | `npm install -g @anthropic-ai/qwen` |
| **Python** | 3.10+ |
| **Node.js** | 18+ (for `npx skills`) |

Make sure `claude`, `codex`, and `qwen` all work from your terminal before proceeding.

## Install

### via npm (recommended)

```bash
npm install -g ai-orchestrator
```

This installs the `unicode` command globally and automatically installs all Python dependencies.

### via npx (no install required)

```bash
# Always runs the latest published version
npx ai-orchestrator@latest <task>

# Or omit the tag (uses latest by default)
npx ai-orchestrator <task>
```

> This project is updated frequently. We recommend running `npx ai-orchestrator@latest` whenever you feel like picking up new improvements — no reinstall needed.

### from source

```bash
git clone https://github.com/Abedishere/unicode.git
cd unicode
pip install -e .
```

## Usage

```bash
# Interactive — prompts for task, tier, and working directory
unicode

# Pass a task directly
unicode --task "build a REST API with Flask"

# Specify working directory and tier
unicode --task "add authentication" --working-dir ./my-project --tier standard

# Auto-approve all gates (non-interactive / CI mode)
unicode --auto

# Commit and push on completion
unicode --auto-commit --push

# Force a specific developer model
unicode --dev-model opus

# Resume a previous session
unicode --resume
```

## Task Complexity Tiers

Select at startup or via `--tier quick|standard|complex`:

| Tier | Developer Model | Review Cycles | Discussion Rounds |
|---|---|---|---|
| `quick` | claude-sonnet | 1 | 1 |
| `standard` | claude-sonnet | 2 | 2 |
| `complex` | claude-opus | 3 | 4 |

## Controls

| Key | Action |
|---|---|
| `Ctrl+C` | Cancel current agent — skip to review phase |
| `Ctrl+Z` | Cancel current agent — skip to finalization |

## Approval Gates

Before each phase you are prompted:

| Response | Action |
|---|---|
| `y` / `yes` | Proceed |
| `n` / `no` | Skip this step |
| `e` / `edit` | Pause and provide additional instructions |
| `a` / `all` | Auto-approve all remaining gates this session |

## Configuration

Edit `config.yaml` in the project root:

```yaml
discussion_rounds: 4
max_review_iterations: 3
claude_model: "opus"
codex_model: "gpt-5.3-codex"
qwen_model: "qwen3-coder"
dev_model: "sonnet"
timeout_seconds: 600
codex_timeout_seconds: 300
auto_commit: false
working_directory: "."

# Optimization settings
repo_map_max_tokens: 2000
discussion_summary_window: 4
file_by_file_generation: true

tiers:
  quick:
    dev_model: "sonnet"
    max_review_iterations: 1
    discussion_rounds: 1
  standard:
    dev_model: "sonnet"
    max_review_iterations: 2
    discussion_rounds: 2
  complex:
    dev_model: "opus"
    max_review_iterations: 3
    discussion_rounds: 4
```

## Token Optimization

Unicode minimizes token usage across all phases:

- **Repo skeleton map** — compressed AST-like view of the codebase (class names, function signatures, imports) injected into discussion, plan, and implementation prompts. Typically ~1-2K tokens for an entire codebase. Configurable via `repo_map_max_tokens`.
- **Sliding window discussion** — old rounds are summarized to ~150 chars each; only the last 2 exchanges are kept verbatim. Token growth is linear instead of quadratic. Configurable via `discussion_summary_window`.
- **File-by-file generation** — when the plan outputs structured per-file specs, implementation breaks into focused per-file calls (skeleton + shared deps + file spec only). Enables larger projects without hitting context limits. Falls back to monolithic if the plan doesn't parse. Configurable via `file_by_file_generation`.
- **Tiered diff review** — reviewers receive a structured diff summary (files changed, functions added/modified/removed) instead of the full diff. They can request full diffs for specific files by responding with `NEED_FULL_DIFF: filename`. Roughly halves review tokens for clean implementations.
- **Cached memory context** — memory context (YAML index + markdown notes) is computed once per task and reused across all phases. Previously recomputed redundantly per phase.

## Skills Ecosystem

Unicode integrates with the [`npx skills`](https://github.com/vercel-labs/skills) ecosystem. Two skills are bundled and available to all agents:

### find-skills
Lets agents search for and install new skills from the registry when they need specialized knowledge.

```bash
# Install (already included, but to update)
npx skills add https://github.com/vercel-labs/skills --skill find-skills -g
```

Agents can run `npx skills find [query]` to discover skills on demand.

### project-memory
Defines a structured format for persistent project notes in `.orchestrator/`. The orchestrator writes to these files automatically at the end of every task.

```bash
# Install (already included, but to update)
npx skills add https://github.com/spillwavesolutions/project-memory --skill project-memory -g
```

Skills are installed to `.agents/skills/` (universal) and symlinked into `.claude/skills/`, `.qwen/skills/`, and `~/.codex/skills/` so every agent picks them up natively.

## Persistent Memory

After each run, unicode maintains two parallel memory stores in your working directory:

**YAML index** (`.orchestrator/memory.yaml`) — machine-queryable, auto-injected into every agent prompt:
- Architecture decisions, codebase conventions, past mistakes, task history

**Markdown notes** (`.orchestrator/`) — human-readable, follows `project-memory` skill format:

| File | Contents |
|---|---|
| `bugs.md` | Bug log with root causes, solutions, and prevention notes |
| `decisions.md` | Architectural Decision Records (ADRs) — numbered, dated |
| `key_facts.md` | Project config, ports, credentials, important URLs |
| `issues.md` | Work log — completed tasks with outcomes |

Relevant entries from both stores are automatically surfaced in agent prompts for future tasks, giving agents institutional knowledge without manual intervention.

## Files Generated in Your Working Directory

| File | Written by | Purpose |
|---|---|---|
| `CLAUDE.md` | Codex (synthesized) | Project context for Claude Code CLI |
| `AGENTS.md` | Codex (synthesized) | Project context for Codex CLI |
| `orchestrator.md` | Qwen | Full project summary, architecture, folder structure |
| `.orchestrator/bugs.md` | Orchestrator | Structured bug log |
| `.orchestrator/decisions.md` | Orchestrator | Architectural Decision Records |
| `.orchestrator/key_facts.md` | Orchestrator | Project config and key facts |
| `.orchestrator/issues.md` | Orchestrator | Work log |
| `.orchestrator/history.md` | Orchestrator | Run history |
| `.orchestrator/plan.md` | Orchestrator | Latest implementation plan |
| `.orchestrator/memory.yaml` | Orchestrator | Machine-queryable memory index |
