from __future__ import annotations

import os

from agents.base import BaseAgent
from utils.runner import run_cli, run_interactive

# Environment with CLAUDECODE stripped so Claude CLI can run as a subprocess
# even when the orchestrator itself was launched from inside a Claude Code session.
_CLAUDE_ENV = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}


class ClaudeAgent(BaseAgent):
    """Wrapper around the Claude Code CLI (claude -p).

    Supports separate admin and dev models. The *model* is used for admin
    queries (planning, review). The *dev_model* (defaults to *model*) is
    used for implementation tasks.
    """

    def __init__(
        self,
        model: str,
        timeout: int,
        working_dir: str,
        dev_model: str | None = None,
        effort: str | None = None,
        dev_effort: str | None = None,
    ):
        super().__init__(model=model, timeout=timeout, working_dir=working_dir)
        self.dev_model = dev_model or model
        self.effort = effort
        self.dev_effort = dev_effort or effort

    @property
    def name(self) -> str:
        return "Claude"

    def query(self, prompt: str) -> str:
        """Query Claude in admin/read-only mode. No file access."""
        self._maybe_audit(prompt)
        cmd = ["claude", "-p", "--output-format", "text", "--model", self.model]
        if self.effort:
            cmd += ["--effort", self.effort]
        stdout, stderr = run_cli(
            cmd,
            agent_name=self.name,
            input_text=prompt,
            timeout=self.timeout,
            cwd=self.working_dir,
            env=_CLAUDE_ENV,
            no_timeout=True,  # discussion/review queries may run as long as needed; ESC still works
        )
        return self.check_cli_output(stdout, stderr, self.name)

    def implement(self, plan: str) -> str:
        """Run Claude Code as the developer — full file access.

        Uses dev_model instead of admin model.
        """
        cmd = [
            "claude", "-p",
            "--output-format", "text",
            "--model", self.dev_model,
            "--dangerously-skip-permissions",
        ]
        if self.dev_effort:
            cmd += ["--effort", self.dev_effort]
        agent_name = f"{self.name} (dev:{self.dev_model})"
        stdout, stderr = run_cli(
            cmd,
            agent_name=agent_name,
            input_text=plan,
            timeout=self.timeout,
            cwd=self.working_dir,
            env=_CLAUDE_ENV,
            no_timeout=True,
        )
        return self.check_cli_output(stdout, stderr, agent_name)

    def implement_interactive(self, task: str, plan: str) -> int:
        """Run Claude Code interactively — full TUI with streaming output.

        Uses dev_model. The plan is already saved to .orchestrator/plan.md;
        Claude Code reads it directly. Returns the process exit code.
        """
        cmd = [
            "claude",
            "--model", self.dev_model,
            "--dangerously-skip-permissions",
            "Read .orchestrator/plan.md and implement the plan exactly.",
        ]
        if self.dev_effort:
            cmd[3:3] = ["--effort", self.dev_effort]
        return run_interactive(
            cmd,
            agent_name=f"{self.name} (dev:{self.dev_model})",
            timeout=self.timeout,
            cwd=self.working_dir,
            env=_CLAUDE_ENV,
        )
