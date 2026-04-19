#!/usr/bin/env python3
"""
Write ``user_data/system_snapshot.json`` — host + repo + key artifact mtimes (no secrets).

Embeds **swarm keypoints** (annotations) under ``swarm.keypoints`` via
``prediction_agent/swarm_annotations.build_swarm_keypoints`` — stable ids, labels, severities,
and optional ``flow_node`` ids matching the dataflow SVG in ``render_system_snapshot_html.py``.

Adds ``trade_dataflow`` (open trades monitor + optional Bybit WS tape + predict iface position summary)
and ``extra_keypoints`` for the same SVG nodes (``n-ws`` stream, ``n-out`` view).

Usage:
  python3 scripts/write_system_snapshot.py
  python3 scripts/write_system_snapshot.py --out /tmp/snapshot.json

PNG of the rendered HUD (after ``render_system_snapshot_html.py``)::

  .venv/bin/python scripts/system_snapshot_shot.py
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _git_field(cwd: Path, *args: str) -> str | None:
    try:
        r = subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if r.returncode != 0:
        return None
    s = (r.stdout or "").strip()
    return s or None


def _file_entry(root: Path, rel: str) -> dict:
    p = root / rel
    out: dict = {"path": rel, "exists": False}
    try:
        if not p.is_file():
            return out
        st = p.stat()
        out["exists"] = True
        out["size_bytes"] = int(st.st_size)
        out["mtime_utc"] = datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
    except OSError:
        pass
    return out


def _swarm_keypoints_builder(root: Path):
    pa = root / "prediction_agent"
    p = str(pa)
    if p not in sys.path:
        sys.path.insert(0, p)
    from swarm_annotations import build_swarm_keypoints  # noqa: PLC0415

    return build_swarm_keypoints


def _swarm_block(root: Path) -> dict | None:
    sys.path.insert(0, str(root))
    try:
        build_kp = _swarm_keypoints_builder(root)
    except Exception as exc:
        return {"ok": False, "error": str(exc), "keypoints": []}
    try:
        from finance_agent import swarm_knowledge as sk  # noqa: PLC0415
    except Exception as exc:
        return {"ok": False, "error": str(exc), "keypoints": build_kp(None)}
    try:
        out = sk.compute_swarm()
        return {
            "ok": True,
            "swarm_mean": out.get("swarm_mean"),
            "swarm_label": out.get("swarm_label"),
            "swarm_conflict": out.get("swarm_conflict"),
            "sources_n": out.get("sources_n"),
            "missing_files": out.get("missing_files"),
            "keypoints": build_kp(out),
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc), "keypoints": build_kp(None)}


def _read_json_dict(path: Path) -> dict | None:
    if not path.is_file():
        return None
    try:
        o = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return o if isinstance(o, dict) else None


def _trade_dataflow_block(root: Path) -> dict[str, Any]:
    """Open trades (Swarm scope) + WS snapshot + iface tags — keypoints link into dataflow SVG."""
    out: dict[str, Any] = {
        "ok": True,
        "open_trades": None,
        "ws_live": None,
        "iface_position": None,
        "extra_keypoints": [],
    }
    fa = root / "finance_agent"
    if str(fa) not in sys.path:
        sys.path.insert(0, str(fa))
    try:
        from swarm_knowledge import build_open_trades_report  # noqa: PLC0415

        out["open_trades"] = build_open_trades_report()
    except Exception as exc:  # noqa: BLE001
        out["open_trades"] = {"error": str(exc)[:500]}

    ws_path = root / "user_data" / "bybit_ws_monitor_state.json"
    ws_raw = _read_json_dict(ws_path)
    if ws_raw:
        last = ws_raw.get("last_public_trade") if isinstance(ws_raw.get("last_public_trade"), dict) else {}
        out["ws_live"] = {
            "updated_utc": ws_raw.get("updated_utc"),
            "best_bid": ws_raw.get("best_bid"),
            "best_ask": ws_raw.get("best_ask"),
            "public_connected": ws_raw.get("public_connected"),
            "private_connected": ws_raw.get("private_connected"),
            "last_trade_side": last.get("S"),
            "last_trade_price": last.get("p"),
            "last_private_topic": ws_raw.get("last_private_topic"),
        }
        out["extra_keypoints"].append(
            {
                "id": "ws_stream",
                "label": "Bybit WS tape",
                "value": f"bid={ws_raw.get('best_bid')} ask={ws_raw.get('best_ask')}",
                "severity": "neutral",
                "flow_node": "n-ws",
            }
        )

    pos_path = root / "prediction_agent" / "btc_iface_position_tags.json"
    pos_raw = _read_json_dict(pos_path)
    if pos_raw:
        keys = [k for k in pos_raw if not str(k).startswith("_")]
        out["iface_position"] = {"symbols_n": len(keys), "symbols": keys[:12]}

    ot = out["open_trades"]
    if isinstance(ot, dict) and "error" not in ot:
        try:
            n = int(ot.get("open_n") or 0)
        except (TypeError, ValueError):
            n = 0
        src = str(ot.get("source") or "?")
        out["extra_keypoints"].append(
            {
                "id": "ft_open_trades",
                "label": "Open trades (monitor)",
                "value": f"{n} ({src})",
                "severity": "neutral",
                "flow_node": "n-out",
            }
        )
    elif isinstance(ot, dict) and ot.get("error"):
        out["extra_keypoints"].append(
            {
                "id": "ft_open_trades",
                "label": "Open trades (monitor)",
                "value": str(ot.get("error"))[:200],
                "severity": "warn",
                "flow_node": "n-out",
            }
        )

    return out


def build_snapshot(*, root: Path) -> dict:
    rr = root
    dirty = _git_field(rr, "status", "--porcelain")
    artifacts = [
        "prediction_agent/btc_prediction_output.json",
        "prediction_agent/training_channel_output.json",
        "prediction_agent/swarm_knowledge_output.json",
        "letscrash/btc_strategy_0_1_rule_registry.json",
        "user_data/strategy_adaptation.json",
        "user_data/config_btc_futures_dedicated.json",
        "finance_agent/btc_specialist/data/btc_sygnif_ta_snapshot.json",
        "finance_agent/btc_specialist/data/nautilus_strategy_signal.json",
        "finance_agent/btc_specialist/data/nautilus_spot_btc_market_bundle.json",
        "user_data/bybit_ws_monitor_state.json",
        "prediction_agent/btc_iface_position_tags.json",
    ]
    return {
        "schema": "sygnif_system_snapshot",
        "version": 1,
        "generated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "platform": {
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
            "python": sys.version.split()[0],
        },
        "repo": {
            "root": str(rr),
            "git_branch": _git_field(rr, "rev-parse", "--abbrev-ref", "HEAD"),
            "git_commit": _git_field(rr, "rev-parse", "--short", "HEAD"),
            "git_dirty": bool(dirty and dirty.strip()),
        },
        "artifacts": [_file_entry(rr, rel) for rel in artifacts],
        "swarm": _swarm_block(rr),
        "trade_dataflow": _trade_dataflow_block(rr),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Write Sygnif system_snapshot.json")
    ap.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Output path (default: <repo>/user_data/system_snapshot.json)",
    )
    args = ap.parse_args()
    root = _repo_root()
    out_path = args.out or (root / "user_data" / "system_snapshot.json")
    snap = build_snapshot(root=root)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(snap, indent=2) + "\n", encoding="utf-8")
    print(f"[system_snapshot] wrote {out_path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
