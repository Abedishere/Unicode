from agents.base import BaseAgent
from utils.runner import run_cli


class ClaudeAgent(BaseAgent):
    """Wrapper around the Claude Code CLI (claude -p)."""

    @property
    def name(self) -> str:
        return "Claude"

    def query(self, prompt: str) -> str:
        """Query Claude in admin/read-only mode. No file access."""
        cmd = ["claude", "-p", "--output-format", "text", "--model", self.model]
        stdout, stderr = run_cli(
            cmd,
            agent_name=self.name,
            input_text=prompt,
            timeout=self.timeout,
            cwd=self.working_dir,
        )
        if not stdout.strip() and stderr.strip():
            raise RuntimeError(f"Claude CLI failed: {stderr}")
        return stdout.strip()

    def implement(self, plan: str) -> str:
        """Run Claude Code as the developer — full file access."""
        cmd = [
            "claude", "-p",
            "--output-format", "text",
            "--model", self.model,
            "--dangerously-skip-permissions",
        ]
        stdout, stderr = run_cli(
            cmd,
            agent_name=f"{self.name} (developer)",
            input_text=plan,
            timeout=self.timeout,
            cwd=self.working_dir,
        )
        if not stdout.strip() and stderr.strip():
            raise RuntimeError(f"Claude CLI failed: {stderr}")
        return stdout.strip()
