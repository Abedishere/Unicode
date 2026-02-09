from __future__ import annotations

import subprocess
from pathlib import Path


def run_git(args: list[str], cwd: str) -> str:
    """Run a git command and return stdout."""
    result = subprocess.run(
        ["git"] + args,
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout.strip()


def is_git_repo(cwd: str) -> bool:
    try:
        run_git(["rev-parse", "--is-inside-work-tree"], cwd)
        return True
    except (RuntimeError, FileNotFoundError):
        return False


def init_repo(cwd: str) -> None:
    run_git(["init"], cwd)


def create_branch(branch_name: str, cwd: str) -> None:
    run_git(["checkout", "-b", branch_name], cwd)


def get_diff(cwd: str) -> str:
    """Return the full diff of uncommitted changes (staged + unstaged + untracked)."""
    # Stage everything so untracked files appear in the diff
    run_git(["add", "-A"], cwd)
    return run_git(["diff", "--cached"], cwd)


def commit(message: str, cwd: str) -> str:
    run_git(["add", "-A"], cwd)
    return run_git(["commit", "-m", message], cwd)


def push(cwd: str, remote: str = "origin", branch: str | None = None) -> str:
    """Push to remote. Branch defaults to current branch."""
    args = ["push", remote]
    if branch:
        args.append(branch)
    return run_git(args, cwd)
