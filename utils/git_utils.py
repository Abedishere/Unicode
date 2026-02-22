from __future__ import annotations

import os
import subprocess
from pathlib import Path

# Environment passed to every git subprocess we run.
# Suppresses CRLF conversion warnings that flood stderr (and terminal) when
# staging projects with mixed line endings (e.g. .gradle wrapper downloads).
_GIT_ENV = {
    **os.environ,
    "GIT_CONFIG_COUNT": "2",
    "GIT_CONFIG_KEY_0": "core.autocrlf",
    "GIT_CONFIG_VALUE_0": "false",
    "GIT_CONFIG_KEY_1": "core.safecrlf",
    "GIT_CONFIG_VALUE_1": "false",
    # Suppress interactive prompts (credentials, etc.) in non-interactive runs
    "GIT_TERMINAL_PROMPT": "0",
}

# Build artifact and dependency directories excluded from the review diff.
# Staging them is fine (they should be .gitignored by the project anyway),
# but including them in the diff sent to AI reviewers is useless and can
# make the diff so large that the review is broken entirely.
_DIFF_EXCLUDE = [
    # Gradle
    ".gradle/wrapper/dists", ".gradle/caches",
    # Node / JS
    "node_modules",
    # Python
    ".venv", "venv", "env", "__pycache__",
    # Java / Kotlin / Android
    "build", "target", ".idea", "out",
    # Dart / Flutter
    ".dart_tool", ".flutter-plugins", ".flutter-plugins-dependencies",
    # Swift / iOS
    "Pods", ".build",
    # Web
    "dist", ".next", ".nuxt",
    # Generic
    "vendor",
]

# Binary / compiled file extensions excluded from diff (not useful to review)
_DIFF_EXCLUDE_EXT = [
    "*.class", "*.jar", "*.war", "*.ear",
    "*.pyc", "*.pyo", "*.pyd",
    "*.o", "*.so", "*.dll", "*.exe",
    "*.png", "*.jpg", "*.jpeg", "*.gif", "*.webp",
    "*.zip", "*.tar", "*.gz", "*.aar",
]


def _only_warnings(stderr: str) -> bool:
    """Return True if every non-blank stderr line starts with 'warning:'.

    Used to treat permission-denied directory warnings from 'git add -A'
    (caused by Windows junction points / symlinks in user-profile dirs such
    as AppData/Local/Application Data) as non-fatal.  Git still stages all
    files it *can* access; the warning lines are noise we can safely ignore.
    """
    for line in stderr.splitlines():
        line = line.strip()
        if line and not line.startswith("warning:"):
            return False
    return True


def run_git(args: list[str], cwd: str) -> str:
    """Run a git command and return stdout."""
    result = subprocess.run(
        ["git"] + args,
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=_GIT_ENV,
    )
    if result.returncode != 0:
        # Non-zero exit from 'git add -A' is common on Windows when the repo
        # root sits inside (or near) the user profile and git tries to traverse
        # junction-point directories it has no permission to open.  If every
        # stderr line is a warning (no actual errors), the stage succeeded for
        # all accessible files and we can proceed safely.
        if _only_warnings(result.stderr):
            return result.stdout.strip()
        raise RuntimeError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout.strip()


def configure_workspace_git(cwd: str) -> None:
    """Write CRLF-suppression settings into the workspace .git/config.

    Why this is necessary even when _GIT_ENV already has those settings:

    env vars only apply to processes we launch directly.  When Codex runs
    in exec mode it spawns its own sub-shells (bash, cmd.exe) which inherit
    the env — but Codex's *internal* git calls may be invoked via a separate
    process tree that re-reads git config from disk.  Writing to .git/config
    ensures ANY git command run inside this directory (including Codex's own
    git calls) picks up the CRLF suppression setting.

    Also sets diff.renameLimit high so large diffs don't produce "too many
    files changed" warnings that break the diff output.
    """
    settings = {
        "core.autocrlf": "false",
        "core.safecrlf": "false",
        "diff.renameLimit": "0",
    }
    for key, val in settings.items():
        try:
            subprocess.run(
                ["git", "config", key, val],
                cwd=cwd,
                capture_output=True,
                check=False,
                env=_GIT_ENV,
            )
        except Exception:
            pass  # Non-critical — best effort


def is_git_repo(cwd: str) -> bool:
    try:
        run_git(["rev-parse", "--is-inside-work-tree"], cwd)
        return True
    except (RuntimeError, FileNotFoundError):
        return False


def init_repo(cwd: str) -> None:
    run_git(["init"], cwd)
    configure_workspace_git(cwd)


def create_branch(branch_name: str, cwd: str) -> None:
    run_git(["checkout", "-b", branch_name], cwd)


def get_diff(cwd: str) -> str:
    """Return the diff of uncommitted changes, excluding build artifacts.

    Two-step process:
      1. git add -A  — stage everything so new files appear in the diff.
      2. git diff --cached -- <excludes>  — diff only source files.

    Exclusions prevent build artifact directories (.gradle/wrapper/dists,
    node_modules, build/, etc.) from flooding the diff with thousands of
    auto-generated files that AI reviewers cannot meaningfully review.

    CRLF suppression is applied at three layers:
      a. _GIT_ENV passed to every subprocess.run call
      b. -c flags on the git add command
      c. configure_workspace_git() writes to .git/config so that any git
         command run inside this directory (including Codex's internal git
         calls) also respects the settings
    """
    # Layer c: persist settings so Codex's internal git calls also respect them
    configure_workspace_git(cwd)

    # Layer a+b: stage with CRLF suppression flags
    run_git(
        ["-c", "core.autocrlf=false", "-c", "core.safecrlf=false", "add", "-A"],
        cwd,
    )

    # Build the exclusion list for the diff.
    # Use the long-form :(exclude)pattern instead of the :!pattern shorthand.
    # Older Git versions mis-parse :! and treat the next character as another
    # magic token (e.g. the _ in :!__pycache__ becomes "Unimplemented pathspec
    # magic '_'").  The long form is unambiguous on all Git versions.
    path_excludes = [f":(exclude){p}" for p in _DIFF_EXCLUDE]
    ext_excludes = [f":(exclude){e}" for e in _DIFF_EXCLUDE_EXT]
    all_excludes = path_excludes + ext_excludes

    diff = run_git(["diff", "--cached", "--"] + all_excludes, cwd)

    if not diff:
        # Either all changes were in excluded dirs, or nothing changed in
        # source files.  Fall back to the full diff so we don't silently skip.
        diff = run_git(["diff", "--cached"], cwd)

    return diff


def commit(message: str, cwd: str) -> str:
    configure_workspace_git(cwd)
    run_git(["-c", "core.autocrlf=false", "-c", "core.safecrlf=false", "add", "-A"], cwd)
    return run_git(["commit", "-m", message], cwd)


def push(cwd: str, remote: str = "origin", branch: str | None = None) -> str:
    """Push to remote. Branch defaults to current branch."""
    args = ["push", remote]
    if branch:
        args.append(branch)
    return run_git(args, cwd)
