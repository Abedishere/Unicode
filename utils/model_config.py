from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import click
import yaml


GLOBAL_CONFIG_PATH = Path.home() / ".unicode" / "config.yaml"


def load_global_config() -> dict[str, Any]:
    if not GLOBAL_CONFIG_PATH.exists():
        return {}
    try:
        return yaml.safe_load(GLOBAL_CONFIG_PATH.read_text(encoding="utf-8")) or {}
    except OSError:
        return {}


def save_global_config(cfg: dict[str, Any]) -> None:
    GLOBAL_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    GLOBAL_CONFIG_PATH.write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")


def migrate_model_keys(cfg: dict[str, Any]) -> dict[str, Any]:
    """Keep old qwen_model configs working while Kiro becomes the role owner."""
    if "kiro_model" not in cfg and cfg.get("qwen_model"):
        cfg["kiro_model"] = cfg["qwen_model"]
    cfg.setdefault("kiro_model", "haiku")
    cfg.setdefault("claude_effort", "medium")
    cfg.setdefault("dev_effort", "medium")
    cfg.setdefault("codex_model", "gpt-5.5")
    cfg.setdefault("codex_reasoning_effort", "medium")
    return cfg


def should_run_onboarding() -> bool:
    if "pytest" in sys.modules:
        return False
    if os.environ.get("UNICODE_SKIP_ONBOARDING"):
        return False
    if not sys.stdin.isatty():
        return False
    return not bool(load_global_config().get("onboarding_complete"))


def _run_json_command(cmd: list[str], timeout: int = 20) -> Any:
    try:
        result = subprocess.run(
            cmd,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            shell=False,
        )
    except Exception:
        return None
    if result.returncode != 0 or not result.stdout.strip():
        return None
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return result.stdout


def list_kiro_models() -> list[str]:
    data = _run_json_command(["kiro-cli", "chat", "--list-models", "--format", "json"])
    if isinstance(data, list):
        models = []
        for item in data:
            if isinstance(item, str):
                models.append(item)
            elif isinstance(item, dict):
                value = item.get("id") or item.get("model") or item.get("name")
                if value:
                    models.append(str(value))
        return models
    if isinstance(data, dict):
        raw = data.get("models") or data.get("data") or []
        if isinstance(raw, list):
            return [
                str(item.get("id") or item.get("model") or item.get("name") or item)
                for item in raw
                if item
            ]
    if isinstance(data, str):
        return [line.strip() for line in data.splitlines() if line.strip()]
    return []


def _provider_status(command: str) -> str:
    if shutil.which(command) is None:
        return "missing"
    if command == "kiro-cli":
        whoami = _run_json_command(["kiro-cli", "whoami", "--format", "json"], timeout=10)
        return "logged in" if whoami else "installed, login unknown"
    return "installed"


def _prompt_model(label: str, default: str | None, choices: list[str] | None = None) -> str | None:
    choices = choices or []
    if choices:
        click.echo()
        click.echo(click.style(label, fg="cyan", bold=True))
        for idx, model in enumerate(choices[:10], start=1):
            click.echo(f"  {idx}. {model}")
        click.echo("  m. Manual entry")
        choice = click.prompt(
            "Choice",
            default="1" if choices else "m",
            show_default=True,
        ).strip()
        if choice.isdigit():
            index = int(choice) - 1
            if 0 <= index < len(choices):
                return choices[index]
    return click.prompt(label, default=default or "", show_default=bool(default)).strip() or None


def run_first_run_wizard(defaults: dict[str, Any]) -> dict[str, Any]:
    """Prompt once for provider/model defaults and persist them globally."""
    click.echo()
    click.echo(click.style("Unicode first-run setup", fg="cyan", bold=True))
    click.echo("Choose the models Unicode should use by default.")
    click.echo()
    click.echo(f"Claude: {_provider_status('claude')}")
    click.echo(f"Codex:  {_provider_status('codex')}")
    click.echo(f"Kiro:   {_provider_status('kiro-cli')}")
    if shutil.which("kiro-cli") is None:
        click.echo("Kiro CLI was not found. Install/login later with `kiro-cli login`.")

    kiro_models = list_kiro_models() if shutil.which("kiro-cli") else []
    cfg = {
        "onboarding_complete": True,
        "claude_model": _prompt_model(
            "Claude admin model",
            defaults.get("claude_model", "opus"),
            ["opus", "sonnet", "haiku"],
        ),
        "claude_effort": "medium",
        "dev_model": _prompt_model(
            "Claude developer model",
            defaults.get("dev_model", "sonnet"),
            ["sonnet", "opus"],
        ),
        "dev_effort": "medium",
        "codex_model": _prompt_model(
            "Codex model",
            defaults.get("codex_model", "gpt-5.5"),
            [str(defaults["codex_model"])] if defaults.get("codex_model") else [],
        ),
        "codex_reasoning_effort": "medium",
        "kiro_model": _prompt_model(
            "Kiro model for research, init, memory, summaries, and fallback review",
            defaults.get("kiro_model", "haiku"),
            kiro_models,
        ),
    }
    save_global_config(cfg)
    click.echo(click.style(f"Saved defaults to {GLOBAL_CONFIG_PATH}", fg="green"))
    return cfg
