"""License key validation for the unicode CLI."""

from __future__ import annotations

import base64
import os
from datetime import date
from pathlib import Path

# ── Ed25519 public key (base64-encoded raw bytes) ────────────────────────────
# Matches the private key stored at ~/.unicode/private_key.pem (never committed).
_PUBLIC_KEY_B64 = "vvPOpqg2VCnXYLS6wIXCZPA4Fiqlg+vJDr1dxWoBlow="

# ── Where keys are stored locally ────────────────────────────────────────────
_LICENSE_FILE = Path.home() / ".unicode" / "license.key"
_ENV_VAR = "UNICODE_LICENSE_KEY"

# ── Replace with your actual URL ──────────────────────────────────────────────
_PURCHASE_URL = "https://your-site.com"


# ─────────────────────────────────────────────────────────────────────────────

def _pub_key():
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    raw = base64.b64decode(_PUBLIC_KEY_B64)
    return Ed25519PublicKey.from_public_bytes(raw)


def _validate(key_str: str) -> tuple[bool, str]:
    """Return (valid, message). Message is the licensed email on success."""
    try:
        parts = key_str.strip().split(".")
        if len(parts) != 2:
            return False, "Invalid key format."

        payload_b64, sig_b64 = parts
        # urlsafe base64 — restore padding
        payload_bytes = base64.urlsafe_b64decode(payload_b64 + "==")
        sig_bytes     = base64.urlsafe_b64decode(sig_b64     + "==")

        from cryptography.exceptions import InvalidSignature
        try:
            _pub_key().verify(sig_bytes, payload_bytes)
        except InvalidSignature:
            return False, "Invalid license key."

        # Payload format: "email:expiry"  (expiry = "never" | "YYYY-MM-DD")
        payload = payload_bytes.decode()
        if ":" not in payload:
            return False, "Malformed key payload."
        email, expiry = payload.split(":", 1)

        if expiry != "never":
            exp_date = date.fromisoformat(expiry)
            if exp_date < date.today():
                return False, f"License expired on {expiry}."

        return True, f"Licensed to {email}"

    except Exception as exc:
        return False, f"License error: {exc}"


def load_key() -> str | None:
    """Return the stored key string, or None if not found."""
    env = os.environ.get(_ENV_VAR)
    if env:
        return env.strip()
    if _LICENSE_FILE.exists():
        return _LICENSE_FILE.read_text().strip()
    return None


def check_license() -> None:
    """Validate the license. Prints an error and exits if invalid or missing.
    Call this before any other logic in main()."""
    from rich.console import Console
    from rich.panel import Panel
    console = Console()

    key = load_key()
    if not key:
        console.print(Panel(
            "No license key found.\n\n"
            f"Run [bold]unicode activate <key>[/] to activate.\n"
            f"Get a license at [bold cyan]{_PURCHASE_URL}[/]",
            title="[yellow]License Required[/]",
            border_style="yellow",
        ))
        raise SystemExit(1)

    valid, msg = _validate(key)
    if not valid:
        console.print(Panel(
            f"[red]{msg}[/]\n\n"
            f"Run [bold]unicode activate <key>[/] with a valid key.\n"
            f"Get a license at [bold cyan]{_PURCHASE_URL}[/]",
            title="[red]License Error[/]",
            border_style="red",
        ))
        raise SystemExit(1)


def activate(key_str: str) -> None:
    """Validate then save a license key locally."""
    from rich.console import Console
    from rich.panel import Panel
    console = Console()

    valid, msg = _validate(key_str)
    if not valid:
        console.print(Panel(
            f"[red]{msg}[/]",
            title="[red]Activation Failed[/]",
            border_style="red",
        ))
        raise SystemExit(1)

    _LICENSE_FILE.parent.mkdir(parents=True, exist_ok=True)
    _LICENSE_FILE.write_text(key_str.strip())
    console.print(Panel(
        f"[green]{msg}[/]\n\n"
        "License saved. Run [bold]unicode[/] to get started.",
        title="[green]Activated[/]",
        border_style="green",
    ))
