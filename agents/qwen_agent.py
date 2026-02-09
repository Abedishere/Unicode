from agents.base import BaseAgent
from utils.runner import run_cli


class QwenAgent(BaseAgent):
    """Wrapper around the Qwen CLI (qwen -p)."""

    @property
    def name(self) -> str:
        return "Qwen"

    def query(self, prompt: str) -> str:
        cmd = ["qwen", "-p", "-o", "text", "--model", self.model]
        stdout, stderr = run_cli(
            cmd,
            agent_name=self.name,
            input_text=prompt,
            timeout=self.timeout,
            cwd=self.working_dir,
        )
        if not stdout.strip() and stderr.strip():
            raise RuntimeError(f"Qwen CLI failed: {stderr}")
        return stdout.strip()
