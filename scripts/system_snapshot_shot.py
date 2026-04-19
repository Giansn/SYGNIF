#!/usr/bin/env python3
"""
Regenerate **system snapshot** (JSON + HTML) and save a **PNG screenshot** of the HUD page.

Requires Playwright + Chromium in the repo venv::

  cd ~/SYGNIF && .venv/bin/pip install playwright && .venv/bin/playwright install chromium

Usage::

  .venv/bin/python scripts/system_snapshot_shot.py
  .venv/bin/python scripts/system_snapshot_shot.py --out user_data/system_snapshot_shot.png --width 1600
  .venv/bin/python scripts/system_snapshot_shot.py --no-refresh   # only screenshot existing HTML
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


def _repo() -> Path:
    return Path(__file__).resolve().parents[1]


def _run_snapshot_pipeline(py: Path, *, refresh: bool) -> int:
    if not refresh:
        return 0
    root = _repo()
    for rel in ("scripts/write_system_snapshot.py", "scripts/render_system_snapshot_html.py"):
        script = root / rel
        if not script.is_file():
            print(f"system_snapshot_shot: missing {script}", file=sys.stderr)
            return 2
        r = subprocess.run([str(py), str(script)], cwd=str(root))
        if r.returncode != 0:
            print(f"system_snapshot_shot: {rel} failed rc={r.returncode}", file=sys.stderr)
            return r.returncode or 1
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="PNG screenshot of user_data/system_snapshot.html")
    ap.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Output PNG (default: user_data/system_snapshot_shot_<UTC>.png)",
    )
    ap.add_argument("--width", type=int, default=1440, help="Viewport width (default 1440)")
    ap.add_argument("--height", type=int, default=900, help="Viewport height (default 900)")
    ap.add_argument(
        "--no-refresh",
        action="store_true",
        help="Skip write_system_snapshot + render_system_snapshot_html",
    )
    ap.add_argument(
        "--html",
        type=Path,
        default=None,
        help="Input HTML path (default: user_data/system_snapshot.html)",
    )
    args = ap.parse_args()

    try:
        from playwright.sync_api import sync_playwright  # noqa: PLC0415
    except ImportError:
        print(
            "system_snapshot_shot: playwright not installed.\n"
            "  cd ~/SYGNIF && .venv/bin/pip install playwright && .venv/bin/playwright install chromium",
            file=sys.stderr,
        )
        return 2

    root = _repo()
    py = Path(sys.executable)
    rc = _run_snapshot_pipeline(py, refresh=not args.no_refresh)
    if rc != 0:
        return rc

    html_path = (args.html or (root / "user_data" / "system_snapshot.html")).resolve()
    if not html_path.is_file():
        print(f"system_snapshot_shot: missing {html_path}", file=sys.stderr)
        return 3

    out = args.out
    if out is None:
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%SZ")
        out = root / "user_data" / f"system_snapshot_shot_{ts}.png"
    else:
        out = Path(out).expanduser()
    if not out.is_absolute():
        out = (root / out).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)

    url = html_path.as_uri()
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            page = browser.new_page(viewport={"width": args.width, "height": args.height})
            page.goto(url, wait_until="networkidle", timeout=60_000)
            page.wait_for_timeout(1200)
            page.screenshot(path=str(out), full_page=True)
        finally:
            browser.close()

    print(str(out), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
