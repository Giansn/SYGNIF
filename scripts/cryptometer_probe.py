#!/usr/bin/env python3
"""Probe CryptoMeter.io API (key from CRYPTOMETER_API_KEY env only)."""
from __future__ import annotations

import json
import os
import sys
import urllib.parse

import requests

BASE = "https://api.cryptometer.io"
TIMEOUT = 25


def get(path: str, params: dict) -> dict:
    r = requests.get(f"{BASE}{path}", params=params, timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()


def main() -> int:
    key = (os.environ.get("CRYPTOMETER_API_KEY") or "").strip()
    if not key:
        print(
            "Set CRYPTOMETER_API_KEY in the environment (do not commit).\n"
            "  export CRYPTOMETER_API_KEY='your-key'\n"
            "  python3 scripts/cryptometer_probe.py",
            file=sys.stderr,
        )
        return 1

    # Free-tier friendly endpoints per https://www.cryptometer.io/api-doc
    endpoints = [
        (
            "coinlist (bybit_spot, first 5 pairs)",
            "/coinlist/",
            {"e": "bybit_spot", "api_key": key},
        ),
        (
            "trend-indicator-v3",
            "/trend-indicator-v3/",
            {"api_key": key},
        ),
        (
            "rapid-movements (first 3)",
            "/rapid-movements/",
            {"api_key": key},
        ),
    ]

    for label, path, params in endpoints:
        print(f"\n=== {label} ===")
        try:
            j = get(path, params)
        except requests.RequestException as e:
            print(f"HTTP error: {e}", file=sys.stderr)
            continue
        ok = j.get("success") == "true" and j.get("error") == "false"
        if not ok:
            print(json.dumps(j, indent=2)[:2000])
            continue
        data = j.get("data")
        if isinstance(data, list) and data and isinstance(data[0], dict):
            if "pair" in data[0]:
                slim = data[:5]
            else:
                slim = data[:3]
            print(json.dumps(slim, indent=2, ensure_ascii=False))
        elif isinstance(data, list):
            print(json.dumps(data[:10], indent=2, ensure_ascii=False))
        else:
            print(json.dumps(data, indent=2, ensure_ascii=False)[:2000])

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
