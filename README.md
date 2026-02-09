# Unicode — AI Agent Orchestrator

Multi-agent orchestrator that coordinates **Claude Code**, **Codex CLI**, and **Qwen CLI** to collaboratively plan, implement, and review code.

## How it works

```
Plan (Codex drafts, Claude reviews)
  → Discussion (only if they disagree, max 2 rounds)
    → Implementation (Claude as developer, full file access)
      → Code Review (Codex reviews the diff)
        → Finalization (update project files, commit & push)
```

- **Admins** (Claude + Codex) discuss and plan — they don't write code
- **Developer** (Claude) implements the plan with full file access
- **Reviewer** (Codex) reviews the diff for bugs and logic errors
- **Qwen** handles summarization and project documentation

## Prerequisites

You need these CLI tools installed and available on your PATH:

| Tool | Install | Docs |
|------|---------|------|
| **Claude Code** | `npm install -g @anthropic-ai/claude-code` | [claude.ai/claude-code](https://claude.ai/claude-code) |
| **Codex CLI** | `npm install -g @openai/codex` | [github.com/openai/codex](https://github.com/openai/codex) |
| **Qwen CLI** | `npm install -g @anthropic-ai/qwen` | — |
| **Python** | 3.10+ | [python.org](https://python.org) |

Make sure `claude`, `codex`, and `qwen` all work from your terminal before proceeding.

## Install

```bash
git clone https://github.com/Abedishere/unicode.git
cd unicode
pip install -e .
```

This gives you the `unicode` command globally.

## Usage

```bash
# Interactive — prompts you for a task
unicode

# With a task directly
unicode "build a REST API with Flask"

# Override settings
unicode --rounds 2 --working-dir ./my-project "add authentication"
```

## Controls

| Key | Action |
|-----|--------|
| **ESC** | Pause current phase — retry / clarify / skip |
| **Ctrl+C** | Does nothing (first press) |
| **Ctrl+C x2** | Exit unicode (within 2 seconds) |

## Approval gates

Before each phase you get prompted:
- **y** — proceed (one-time)
- **a** — auto-approve this action for the session
- **e** — pause and give instructions
- **n** — skip this step

## Configuration

Edit `config.yaml` in the project root:

```yaml
discussion_rounds: 4
max_review_iterations: 3
claude_model: "opus"
codex_model: "gpt-5.3-codex"
qwen_model: "qwen3-coder"
timeout_seconds: 600
codex_timeout_seconds: 300
auto_commit: false
working_directory: "."
```

## Project files generated

After each run, unicode creates/updates these in your working directory:

| File | Written by | Purpose |
|------|-----------|---------|
| `CLAUDE.md` | Claude | Project context for Claude Code CLI |
| `AGENTS.md` | Codex | Project context for Codex CLI |
| `orchestrator.md` | Qwen | Project summary, architecture, folder structure |
| `.orchestrator/history.md` | Orchestrator | Run history log |
| `.orchestrator/plan.md` | Orchestrator | Latest implementation plan |
