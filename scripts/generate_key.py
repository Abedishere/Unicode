#!/usr/bin/env python3
"""
Issue a unicode license key.
Run this on YOUR machine only — never commit the private key.

Usage:
    python scripts/generate_key.py user@example.com
    python scripts/generate_key.py user@example.com --expiry 2027-12-31
"""

from __future__ import annotations

import argparse
import base64
import sys
from pathlib import Path

PRIVATE_KEY_FILE = Path.home() / ".unicode" / "private_key.pem"


def load_private_key():
    from cryptography.hazmat.primitives.serialization import load_pem_private_key
    if not PRIVATE_KEY_FILE.exists():
        print(f"Error: private key not found at {PRIVATE_KEY_FILE}")
        print("Generate one with: python scripts/generate_key.py --init")
        sys.exit(1)
    return load_pem_private_key(PRIVATE_KEY_FILE.read_bytes(), password=None)


def issue(email: str, expiry: str) -> str:
    priv = load_private_key()
    payload = f"{email}:{expiry}".encode()
    sig = priv.sign(payload)
    p = base64.urlsafe_b64encode(payload).rstrip(b"=").decode()
    s = base64.urlsafe_b64encode(sig).rstrip(b"=").decode()
    return f"{p}.{s}"


def init_keypair():
    """Generate a fresh Ed25519 key pair and print the public key to embed."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives import serialization

    priv = Ed25519PrivateKey.generate()
    pub  = priv.public_key()

    priv_pem = priv.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    pub_raw = pub.public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )

    PRIVATE_KEY_FILE.parent.mkdir(parents=True, exist_ok=True)
    PRIVATE_KEY_FILE.write_bytes(priv_pem)
    PRIVATE_KEY_FILE.chmod(0o600)

    pub_b64 = base64.b64encode(pub_raw).decode()
    print(f"Private key saved to: {PRIVATE_KEY_FILE}")
    print(f"\nPaste this public key into utils/license.py → _PUBLIC_KEY_B64:\n")
    print(f"  {pub_b64}\n")


def main():
    parser = argparse.ArgumentParser(description="Issue a unicode license key")
    parser.add_argument("email", nargs="?", help="Customer email address")
    parser.add_argument("--expiry", default="never",
                        help="Expiry date YYYY-MM-DD, or 'never' (default)")
    parser.add_argument("--init", action="store_true",
                        help="Generate a new Ed25519 key pair")
    args = parser.parse_args()

    if args.init:
        init_keypair()
        return

    if not args.email:
        parser.print_help()
        sys.exit(1)

    key = issue(args.email, args.expiry)
    print(f"\nLicense key for {args.email}  (expires: {args.expiry})\n")
    print(f"  {key}\n")
    print(f"Send the customer this key. They activate with:\n")
    print(f"  unicode activate {key}\n")


if __name__ == "__main__":
    main()
