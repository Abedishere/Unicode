from __future__ import annotations

from agents.base import BaseAgent
from utils.runner import run_cli


class QwenAgent(BaseAgent):
    """Wrapper around the Qwen CLI (qwen -p)."""

    @property
    def name(self) -> str:
        return "Qwen"

    def _base_cmd(self) -> list[str]:
        cmd = ["qwen", "-p", "-o", "text"]
        if self.model:
            cmd += ["--model", self.model]
        return cmd

    def query(self, prompt: str) -> str:
        stdout, stderr = run_cli(
            self._base_cmd(),
            agent_name=self.name,
            input_text=prompt,
            timeout=self.timeout,
            cwd=self.working_dir,
        )
        return self.check_cli_output(stdout, stderr, self.name)

    def research_query(self, prompt: str) -> str:
        """Query Qwen with the web-search MCP server enabled.

        The 'web-search' server (duckduckgo-mcp-server) must be registered
        in ~/.qwen/settings.json — run `qwen mcp add web-search duckduckgo-mcp-server`
        once to set it up.  No API key required; unlimited searches.

        --yolo is required so Qwen auto-approves the MCP tool call without
        waiting for interactive confirmation.
        """
        cmd = self._base_cmd() + ["--yolo", "--allowed-mcp-server-names", "web-search"]
        stdout, stderr = run_cli(
            cmd,
            agent_name=f"{self.name} (research)",
            input_text=prompt,
            timeout=self.timeout,
            cwd=self.working_dir,
        )
        return self.check_cli_output(stdout, stderr, self.name)
