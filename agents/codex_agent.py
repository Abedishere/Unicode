import os
import tempfile
from pathlib import Path

from agents.base import BaseAgent
from utils.git_utils import configure_workspace_git
from utils.runner import run_cli

# Environment for all Codex subprocess invocations.
#
# Why three CRLF-suppression layers?
#
# Layer 1 — GIT_CONFIG_COUNT env vars: suppress CRLF warnings in git
#   commands that Codex spawns as *direct* children (they inherit this env).
#
# Layer 2 — configure_workspace_git() writes to .git/config: suppresses
#   warnings in git commands spawned by Codex's sub-shells.  Sub-shells
#   started by Codex (bash, cmd.exe) may not inherit env vars reliably,
#   but they always read .git/config from the repository root.
#
# Layer 3 — GIT_TERMINAL_PROMPT=0: prevents interactive git prompts
#   (credential dialogs, etc.) from hanging the subprocess.
_CODEX_ENV = {
    **os.environ,
    "GIT_CONFIG_COUNT": "2",
    "GIT_CONFIG_KEY_0": "core.autocrlf",
    "GIT_CONFIG_VALUE_0": "false",
    "GIT_CONFIG_KEY_1": "core.safecrlf",
    "GIT_CONFIG_VALUE_1": "false",
    "GIT_TERMINAL_PROMPT": "0",
}


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

    def query(self, prompt: str) -> str:
        """Query Codex for planning / commit-message / general tasks.

        Uses --sandbox read-only so Codex can inspect the repo but not
        modify it.  Output is captured via --output-last-message written
        to a temp file inside the working directory (where Codex has write
        access even under the read-only sandbox — the sandbox restricts
        *shell commands*, not the CLI's own file writes).

        Why the output file lives inside working_dir:
          --sandbox read-only forbids writes outside the workspace.
          The system temp dir (e.g. C:\\Users\\...\\AppData\\Local\\Temp)
          is outside the workspace, so --output-last-message would silently
          fail there, leaving an empty file and falling through to stdout
          (which may also be empty).  Placing the file inside working_dir
          avoids this entirely.
        """
        # codex exec reads from stdin when prompt is "-"
        # This avoids Windows command line length limits on long prompts.
        # --full-auto: auto-approve every shell command without asking the
        # user for confirmation.  Without this flag codex hangs indefinitely
        # trying to read "approve? [Y/n]" from the console while our stdin
        # pipe is already closed (EOF), causing the subprocess to never exit.
        configure_workspace_git(self.working_dir)
        out_dir = Path(self.working_dir) / ".orchestrator"
        out_dir.mkdir(parents=True, exist_ok=True)
        fd, output_path = tempfile.mkstemp(
            suffix=".txt", prefix="codex_out_", dir=str(out_dir)
        )
        os.close(fd)
        try:
            cmd = [
                "codex", "exec",
                "--model", self.model,
                "--full-auto",
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
                env=_CODEX_ENV,
            )
            # Don't error-check stderr — codex dumps its full session log
            # there (thinking, shell commands, etc.) which often contains
            # the word "error" in innocent contexts. Just read the output file.
            return _read_output(output_path, stdout, stderr)
        finally:
            if os.path.exists(output_path):
                os.unlink(output_path)

    def review_query(self, prompt: str) -> str:
        """Query Codex for text-only code review tasks.

        Why this is separate from query():

        Root cause analysis of Codex failing in review:

        1. `--sandbox read-only` blocks ALL filesystem writes, including
           writes by the Codex CLI itself to --output-last-message.  When
           the output file lives in the system temp dir (outside the
           workspace), --sandbox read-only silently fails the write, leaving
           an empty file.  Moving the file into working_dir partially helps
           (see query()), but for review tasks the full sandbox can still
           interfere with how Codex flushes its final message.

        2. Codex in exec mode is trained to RUN COMMANDS.  Even with "do not
           run commands" in the prompt, Codex frequently runs `git log`,
           `git show`, or `cat` to inspect context.  The --output-last-message
           flag captures the model's LAST assistant turn — if that turn is
           "I'll verify by running git log..." followed by command execution,
           the captured text is git output, not the review commentary.

        3. Removing --sandbox for review tasks:
           - The diff is provided INLINE in the prompt — Codex does not need
             filesystem access at all.
           - Without sandbox restrictions, --output-last-message reliably
             writes the final text response.
           - We add explicit NO-TOOLS instructions at the top of the prompt
             (see _codex_primary_review in review.py) to suppress command use.
        """
        # Layer 2 CRLF suppression: write to .git/config so even Codex's
        # sub-shell git calls (which may not inherit env vars) pick up the
        # autocrlf=false setting.  Called here as well as in get_diff() so
        # this is guaranteed to be set immediately before Codex launches.
        configure_workspace_git(self.working_dir)

        out_dir = Path(self.working_dir) / ".orchestrator"
        out_dir.mkdir(parents=True, exist_ok=True)
        fd, output_path = tempfile.mkstemp(
            suffix=".txt", prefix="codex_review_", dir=str(out_dir)
        )
        os.close(fd)
        try:
            cmd = [
                "codex", "exec",
                "--model", self.model,
                "--full-auto",
                # No --sandbox: diff is provided inline; Codex only needs to
                # output text.  Removing the sandbox ensures --output-last-message
                # can always write to the output file.
                "--output-last-message", output_path,
                "-",
            ]
            stdout, stderr = run_cli(
                cmd,
                agent_name=f"{self.name} (review)",
                input_text=prompt,
                timeout=self.timeout,
                cwd=self.working_dir,
                env=_CODEX_ENV,
            )
            return _read_output(output_path, stdout, stderr)
        finally:
            if os.path.exists(output_path):
                os.unlink(output_path)
