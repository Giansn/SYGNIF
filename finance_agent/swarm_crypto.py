#!/usr/bin/env python3
"""
Swarm payload sealing — **Fernet** (AES-128-CBC + HMAC-SHA256, token format).

Efficient pipeline: ``json.dumps(..., separators=(',', ':'))`` → single ``encrypt()`` blob.
Requires: ``pip install cryptography`` and a key from ``Fernet.generate_key()``.

Env:
  ``SYGNIF_SWARM_FERNET_KEY`` — urlsafe base64 **44-char** key (keep in secrets, not git).

**Not** post-quantum; for **at-rest** obscurity of swarm JSON on shared disks — not a substitute
for filesystem permissions or HSM custody.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any


def fernet_cipher():
    try:
        from cryptography.fernet import Fernet  # noqa: PLC0415
    except ImportError:
        return None
    raw = os.environ.get("SYGNIF_SWARM_FERNET_KEY", "").strip()
    if not raw:
        return None
    try:
        return Fernet(raw.encode("utf-8") if isinstance(raw, str) else raw)
    except Exception:
        return None


def seal_swarm_dict(payload: dict[str, Any]) -> str:
    """Return ASCII token (Fernet). Raises if key/lib missing."""
    f = fernet_cipher()
    if f is None:
        raise RuntimeError(
            "Sealing requires cryptography package and valid SYGNIF_SWARM_FERNET_KEY "
            "(generate: python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\")"
        )
    raw = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    tok = f.encrypt(raw)
    return tok.decode("ascii")


def unseal_swarm_token(token: str) -> dict[str, Any]:
    """Decrypt Fernet token → dict."""
    f = fernet_cipher()
    if f is None:
        raise RuntimeError("Decrypt requires cryptography and SYGNIF_SWARM_FERNET_KEY")
    if isinstance(token, str):
        token_b = token.encode("ascii")
    else:
        token_b = token
    raw = f.decrypt(token_b)
    return json.loads(raw.decode("utf-8"))


def wrap_sealed_envelope(token: str) -> dict[str, Any]:
    """JSON-safe wrapper for one-line files / HTTP."""
    return {
        "schema": "sygnif_swarm_sealed",
        "version": 1,
        "alg": "fernet",
        "payload": token,
    }


def unwrap_sealed_envelope(obj: dict[str, Any]) -> dict[str, Any]:
    if obj.get("schema") != "sygnif_swarm_sealed":
        raise ValueError("not a sygnif_swarm_sealed envelope")
    tok = obj.get("payload")
    if not isinstance(tok, str):
        raise ValueError("missing payload string")
    return unseal_swarm_token(tok)


def main() -> int:
    ap = argparse.ArgumentParser(description="Seal / open swarm crypto envelopes.")
    ap.add_argument("--decrypt-file", type=Path, help="JSON file with schema sygnif_swarm_sealed")
    ap.add_argument("--print-json", action="store_true", help="Pretty-print decrypted JSON")
    args = ap.parse_args()
    if args.decrypt_file:
        try:
            env = json.loads(args.decrypt_file.read_text(encoding="utf-8"))
        except OSError as exc:
            print(exc, file=sys.stderr)
            return 1
        if not isinstance(env, dict):
            print("file must be a JSON object", file=sys.stderr)
            return 1
        try:
            out = unwrap_sealed_envelope(env)
        except Exception as exc:
            print(f"decrypt failed: {exc}", file=sys.stderr)
            return 1
        if args.print_json:
            print(json.dumps(out, indent=2))
        else:
            print(json.dumps(out, separators=(",", ":")))
        return 0
    ap.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
