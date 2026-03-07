import os
import tempfile
from pathlib import Path

from agents.base import BaseAgent
from utils.git_utils import _GIT_ENV, configure_workspace_git
from utils.runner import run_cli

# _GIT_ENV is imported from utils.git_utils — it sets three CRLF-suppression
# layers for git commands that Codex spawns (direct children, sub-shells,
# and GIT_TERMINAL_PROMPT=0 to prevent interactive credential prompts).


def _read_output(output_path: str, stdout: str, stderr: str) -> str:
    """Read Codex output from the temp file, falling back to stdout.

    Raises RuntimeError if both are empty.
    """
    try:
        with open(output_path, "r", encoding="utf-8") as f:
            result = f.read().strip()
    except OSError:
        result = ""

    if not result and stdout.strip():
        result = stdout.strip()
    if not result:
        raise RuntimeError(
            f"Codex returned empty response. stderr tail: {stderr[-500:]}"
        )
    return result


class CodexAgent(BaseAgent):
    """Wrapper around the Codex CLI (codex exec)."""

    @property
    def name(self) -> str:
        return "Codex"

    def _run_codex(self, prompt: str, *, sandbox: bool, agent_suffix: str = "") -> str:
        """Common codex exec invocation.

        sandbox=True adds --sandbox read-only (used for planning/general tasks
        so Codex can inspect the repo but not modify it). Output file is placed
        inside working_dir because --sandbox read-only forbids writes to the
        system temp dir.

        sandbox=False omits the sandbox flag (used for review tasks where the
        diff is provided inline and reliable --output-last-message writes are
        needed). See review_query() docstring for the full rationale.
        """
        configure_workspace_git(self.working_dir)
        out_dir = Path(self.working_dir) / ".orchestrator"
        out_dir.mkdir(parents=True, exist_ok=True)
        prefix = "codex_out_" if sandbox else "codex_review_"
        fd, output_path = tempfile.mkstemp(suffix=".txt", prefix=prefix, dir=str(out_dir))
        os.close(fd)
        try:
            cmd = ["codex", "exec", "--model", self.model, "--full-auto"]
            if sandbox:
                cmd += ["--sandbox", "read-only"]
            cmd += ["--output-last-message", output_path, "-"]
            agent_name = f"{self.name} ({agent_suffix})" if agent_suffix else self.name
            stdout, stderr = run_cli(
                cmd,
                agent_name=agent_name,
                input_text=prompt,
                timeout=self.timeout,
                cwd=self.working_dir,
                env=_GIT_ENV,
            )
            # Don't error-check stderr — Codex dumps its full session log
            # there (thinking, shell commands, etc.) which often contains
            # the word "error" in innocent contexts. Just read the output file.
            return _read_output(output_path, stdout, stderr)
        finally:
            if os.path.exists(output_path):
                os.unlink(output_path)

    def query(self, prompt: str) -> str:
        """Query Codex for planning / commit-message / general tasks."""
        # --full-auto: auto-approve every shell command without prompting.
        # Without this, codex hangs on "approve? [Y/n]" when stdin is EOF.
        return self._run_codex(prompt, sandbox=True)

    def review_query(self, prompt: str) -> str:
        """Query Codex for text-only code review tasks.

        Why sandbox=False: --sandbox read-only can interfere with
        --output-last-message writes for review tasks, and the diff is
        provided inline so filesystem access is not needed. See the long
        comment in the original implementation for full root-cause analysis.
        """
        return self._run_codex(prompt, sandbox=False, agent_suffix="review")
