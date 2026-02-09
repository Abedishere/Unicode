import os
import tempfile

from agents.base import BaseAgent
from utils.runner import run_cli


class CodexAgent(BaseAgent):
    """Wrapper around the Codex CLI (codex exec)."""

    @property
    def name(self) -> str:
        return "Codex"

    def query(self, prompt: str) -> str:
        # codex exec reads from stdin when prompt is "-"
        # This avoids Windows command line length limits on long prompts
        fd, output_path = tempfile.mkstemp(suffix=".txt", prefix="codex_out_")
        os.close(fd)
        try:
            cmd = [
                "codex", "exec",
                "--model", self.model,
                "--sandbox", "read-only",
                "--output-last-message", output_path,
                "-",
            ]
            stdout, stderr = run_cli(
                cmd,
                agent_name=self.name,
                input_text=prompt,
                timeout=self.timeout,
                cwd=self.working_dir,
            )
            # Don't error-check stderr — codex dumps its full session log
            # there (thinking, shell commands, etc.) which often contains
            # the word "error" in innocent contexts. Just read the output file.

            with open(output_path, "r", encoding="utf-8") as f:
                result = f.read().strip()
            # If output file is empty, fall back to stdout
            if not result and stdout.strip():
                result = stdout.strip()
            if not result:
                raise RuntimeError(
                    f"Codex returned empty response. stderr tail: {stderr[-500:]}"
                )
            return result
        finally:
            if os.path.exists(output_path):
                os.unlink(output_path)
