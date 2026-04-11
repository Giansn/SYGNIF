#!/usr/bin/env python3
"""Sample FinancialData.net API calls (requires FINANCIALDATA_API_KEY in .env or env).

Docs: https://financialdata.net/documentation — auth: append ?key= or &key= to each URL.
Subscription labels in the docs (Free / Standard / Premium) vary by endpoint; this script
tries Free-friendly routes first, then optional Premium routes (may 403 on Free tier).
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path


def load_financialdata_key() -> str:
    key = os.environ.get("FINANCIALDATA_API_KEY", "").strip()
    if key:
        return key
    env_file = Path(__file__).resolve().parents[1] / ".env"
    if not env_file.is_file():
        return ""
    for line in env_file.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("FINANCIALDATA_API_KEY="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


def get_json(url: str) -> dict | list:
    req = urllib.request.Request(url, headers={"User-Agent": "SYGNIF-fetch-financialdata/1"})
    with urllib.request.urlopen(req, timeout=45) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main() -> int:
    key = load_financialdata_key()
    if not key:
        print(
            "Set FINANCIALDATA_API_KEY in repo .env or environment.\n"
            "See https://financialdata.net/documentation — append &key= to requests.",
            file=sys.stderr,
        )
        return 1
    base = "https://financialdata.net/api/v1"
    # Tier hints from https://financialdata.net/documentation (verify on your plan).
    paths = [
        ("stock-prices?identifier=MSFT", "Free (per docs)"),
        ("commodity-prices?identifier=ZC", "Free (per docs)"),
        ("crypto-quotes?identifiers=BTCUSD,ETHUSD", "Premium (per docs)"),
        ("crypto-information?identifier=BTC", "Standard (per docs)"),
        ("index-quotes?identifiers=^GSPC,^DJI", "Premium (per docs)"),
    ]
    print("Docs: https://financialdata.net/documentation\n")
    for path, tier in paths:
        sep = "&" if "?" in path else "?"
        url = f"{base}/{path}{sep}key={key}"
        label = path.split("?")[0]
        try:
            data = get_json(url)
        except urllib.error.HTTPError as e:
            err = e.read().decode("utf-8", errors="replace")[:2000]
            print(f"\n=== {label} [{tier}] HTTP {e.code} ===\n{err}\n", file=sys.stderr)
            continue
        except Exception as e:
            print(f"\n=== {label} [{tier}] ERROR {e!r}\n", file=sys.stderr)
            continue
        print(f"\n=== {label} [{tier}] ===")
        print(json.dumps(data, indent=2)[:16000])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
