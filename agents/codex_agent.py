from __future__ import annotations

import os
import tempfile
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:          # Python < 3.11
    try:
        import tomli as tomllib      # type: ignore[no-redef]
    except ModuleNotFoundError:
        tomllib = None               # type: ignore[assignment]

from agents.base import BaseAgent
from utils.git_utils import _GIT_ENV, configure_workspace_git
from utils.runner import run_cli


def read_codex_config() -> dict:
    """Read model and reasoning effort from ~/.codex/config.toml."""
    cfg_path = Path.home() / ".codex" / "config.toml"
    if not cfg_path.exists() or tomllib is None:
        return {}
    with open(cfg_path, "rb") as f:
        data = tomllib.load(f)
    result = {}
    if "model" in data:
        result["model"] = data["model"]
    if "model_reasoning_effort" in data:
        result["reasoning_effort"] = data["model_reasoning_effort"]
    return result


def _read_output(output_path: str, stdout: str, stderr: str) -> str:
    """Read Codex output from the temp file, falling back to stdout."""
    from utils.runner import UsageLimitReached, _is_usage_limit
    if _is_usage_limit(stdout, stderr):
        raise UsageLimitReached("Codex", (stdout + stderr)[-300:])

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
    """Wrapper around the Codex CLI (codex exec).

    When *model* is ``None``, the ``--model`` flag is omitted so the Codex CLI
    falls back to whatever is set in ``~/.codex/config.toml``.
    """

    @property
    def name(self) -> str:
        return "Codex"

    def __init__(
        self,
        model: str | None,
        timeout: int,
        working_dir: str,
        reasoning_effort: str | None = None,
    ):
        super().__init__(model=model, timeout=timeout, working_dir=working_dir)
        self.reasoning_effort = reasoning_effort

    def _run_codex(self, prompt: str, *, sandbox: bool, agent_suffix: str = "") -> str:
        """Common codex exec invocation.

        sandbox=True adds --sandbox read-only (used for planning/general tasks
        so Codex can inspect the repo but not modify it).

        sandbox=False omits the sandbox flag (used for review tasks where the
        diff is provided inline and reliable --output-last-message writes are
        needed).
        """
        configure_workspace_git(self.working_dir)
        out_dir = Path(self.working_dir) / ".orchestrator"
        out_dir.mkdir(parents=True, exist_ok=True)
        prefix = "codex_out_" if sandbox else "codex_review_"
        fd, output_path = tempfile.mkstemp(suffix=".txt", prefix=prefix, dir=str(out_dir))
        os.close(fd)
        try:
            cmd = ["codex", "exec"]
            if self.model:
                cmd += ["--model", self.model]
            if self.reasoning_effort:
                cmd += ["-c", f'model_reasoning_effort="{self.reasoning_effort}"']
            cmd += ["--full-auto"]
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
                quiet=self._quiet,
            )
            return _read_output(output_path, stdout, stderr)
        finally:
            if os.path.exists(output_path):
                os.unlink(output_path)

    def query(self, prompt: str) -> str:
        """Query Codex for planning / commit-message / general tasks."""
        self._maybe_audit(prompt)
        return self._run_codex(prompt, sandbox=True)

    def implement(self, prompt: str) -> str:
        """Run Codex with write access for implementation fallback work."""
        self._maybe_audit(prompt)
        return self._run_codex(prompt, sandbox=False, agent_suffix="implement")

    def review_query(self, prompt: str) -> str:
        """Query Codex for text-only code review tasks."""
        return self._run_codex(prompt, sandbox=False, agent_suffix="review")
