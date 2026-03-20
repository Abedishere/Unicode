from __future__ import annotations

from agents.base import BaseAgent
from utils.runner import run_cli


class QwenAgent(BaseAgent):
    """Wrapper around the Qwen CLI (qwen -p)."""

    @property
    def name(self) -> str:
        return "Qwen"

    def query(self, prompt: str) -> str:
        cmd = ["qwen", "-p", "-o", "text"]
        if self.model:
            cmd += ["--model", self.model]
        stdout, stderr = run_cli(
            cmd,
            agent_name=self.name,
            input_text=prompt,
            timeout=self.timeout,
            cwd=self.working_dir,
        )
        return self.check_cli_output(stdout, stderr, self.name)
