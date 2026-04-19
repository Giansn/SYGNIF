#!/usr/bin/env python3
"""
Read-only **Truthcoin Drivechain** (Bitcoin Hivemind) snapshot for Swarm ``btc_future`` context.

The upstream project is described in
`Truthcoin Drivechain README <https://github.com/LayerTwo-Labs/truthcoin-dc/blob/master/README.md>`__
(prediction markets, slots, SVD voting). The desktop app adds **Block** and **Mempool** explorer
tabs; regtest stacks also use **Electrs** HTTP (see ``docs/INSTALL_LINUX.md``).

This module shells out to ``truthcoin_dc_app_cli`` (JSON where the CLI emits it: ``slot-status``,
``slot-list``; human-readable for ``status`` / ``market-list``). It does **not** talk to Bybit and
does **not** add a Swarm vote — it only enriches ``compute_swarm()`` → ``btc_future.hivemind_explore``.

Env:

- ``SYGNIF_SWARM_TRUTHCOIN_DC`` — ``1`` / ``true`` / … to enable (often set with ``SYGNIF_SWARM_BTC_FUTURE=1`` or ``trade``).
- ``SYGNIF_TRUTHCOIN_DC_ROOT`` — repo root used as subprocess ``cwd`` (default ``~/truthcoin-dc``).
- ``SYGNIF_TRUTHCOIN_DC_CLI`` — path to ``truthcoin_dc_app_cli`` (otherwise ``PATH`` or
  ``<TRUTHCOIN_DC_ROOT>/target/debug/truthcoin_dc_app_cli``).
- ``SYGNIF_TRUTHCOIN_DC_RPC_HOST`` / ``SYGNIF_TRUTHCOIN_DC_RPC_PORT`` — passed to the CLI (default port **6013**).
- ``SYGNIF_TRUTHCOIN_DC_TIMEOUT_SEC`` — per-invocation timeout (default **5**).
- ``SYGNIF_TRUTHCOIN_DC_CACHE_SEC`` — in-process cache TTL (default **30**, ``0`` disables).
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

README_REF = "https://github.com/LayerTwo-Labs/truthcoin-dc/blob/master/README.md"

_CACHE: tuple[float, dict[str, Any]] | None = None


def truthcoin_dc_repo_root() -> Path:
    """Working tree for Truthcoin DC (CLI ``cwd`` and binary search)."""
    raw = (os.environ.get("SYGNIF_TRUTHCOIN_DC_ROOT") or "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    return (Path.home() / "truthcoin-dc").resolve()


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _env_truthy(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")


def resolve_truthcoin_cli() -> Path | None:
    raw = (os.environ.get("SYGNIF_TRUTHCOIN_DC_CLI") or "").strip()
    if raw:
        p = Path(raw).expanduser()
        if p.is_file() and os.access(p, os.X_OK):
            return p.resolve()
    which = shutil.which("truthcoin_dc_app_cli")
    if which:
        return Path(which).resolve()
    root = truthcoin_dc_repo_root()
    guess = root / "target" / "debug" / "truthcoin_dc_app_cli"
    if guess.is_file() and os.access(guess, os.X_OK):
        return guess.resolve()
    rel = root / "target" / "release" / "truthcoin_dc_app_cli"
    if rel.is_file() and os.access(rel, os.X_OK):
        return rel.resolve()
    return None


def _base_cmd(cli: Path) -> list[str]:
    host = (os.environ.get("SYGNIF_TRUTHCOIN_DC_RPC_HOST") or "127.0.0.1").strip() or "127.0.0.1"
    port = (os.environ.get("SYGNIF_TRUTHCOIN_DC_RPC_PORT") or "6013").strip() or "6013"
    return [str(cli), "--rpc-host", host, "--rpc-port", port]


def _run_cli(
    cli: Path,
    args: list[str],
    *,
    timeout: float,
    cwd: Path | None = None,
) -> tuple[int, str, str]:
    argv = _base_cmd(cli) + args
    tc_root = truthcoin_dc_repo_root()
    run_cwd = str(cwd) if cwd is not None else (str(tc_root) if tc_root.is_dir() else None)
    try:
        p = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=max(0.25, timeout),
            cwd=run_cwd,
        )
    except subprocess.TimeoutExpired:
        return 124, "", "timeout"
    except OSError as exc:
        return 125, "", str(exc)
    return int(p.returncode), (p.stdout or "").strip(), (p.stderr or "").strip()


def _parse_json_maybe(text: str) -> Any:
    text = (text or "").strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def _market_list_total(text: str) -> int | None:
    m = re.search(r"Total markets:\s*(\d+)", text or "", flags=re.IGNORECASE)
    if not m:
        return None
    try:
        return int(m.group(1))
    except ValueError:
        return None


def hivemind_explore_snapshot() -> dict[str, Any]:
    """
    Return a JSON-serializable dict for ``btc_future.hivemind_explore``.

    Safe to call when disabled or CLI missing — returns ``ok: false`` with a short reason.
    """
    global _CACHE
    if not _env_truthy("SYGNIF_SWARM_TRUTHCOIN_DC"):
        return {"enabled": False, "ok": False, "detail": "SYGNIF_SWARM_TRUTHCOIN_DC off"}

    ttl = _env_float("SYGNIF_TRUTHCOIN_DC_CACHE_SEC", 30.0)
    now = time.monotonic()
    if ttl > 0 and _CACHE is not None:
        ts, doc = _CACHE
        if now - ts < ttl:
            return dict(doc)

    cli = resolve_truthcoin_cli()
    if cli is None:
        out = {
            "enabled": True,
            "ok": False,
            "readme": README_REF,
            "detail": "truthcoin_dc_app_cli not found (set SYGNIF_TRUTHCOIN_DC_CLI or build truthcoin-dc)",
        }
        if ttl > 0:
            _CACHE = (now, out)
        return out

    timeout = _env_float("SYGNIF_TRUTHCOIN_DC_TIMEOUT_SEC", 5.0)
    doc: dict[str, Any] = {
        "enabled": True,
        "ok": True,
        "readme": README_REF,
        "cli": str(cli),
        "rpc_host": (os.environ.get("SYGNIF_TRUTHCOIN_DC_RPC_HOST") or "127.0.0.1").strip()
        or "127.0.0.1",
        "rpc_port": (os.environ.get("SYGNIF_TRUTHCOIN_DC_RPC_PORT") or "6013").strip() or "6013",
        "explore": {
            "note": "GUI: Block Explorer + Mempool Explorer tabs; Electrs HTTP :3000 on regtest (INSTALL_LINUX).",
        },
    }

    rc_st, out_st, err_st = _run_cli(cli, ["status"], timeout=timeout)
    doc["status_rc"] = rc_st
    doc["status_text"] = out_st[:4000] if out_st else ""
    if err_st:
        doc["status_stderr"] = err_st[:2000]

    rc_ss, out_ss, err_ss = _run_cli(cli, ["slot-status"], timeout=timeout)
    doc["slot_status_rc"] = rc_ss
    parsed_ss = _parse_json_maybe(out_ss)
    if parsed_ss is not None:
        doc["slot_status"] = parsed_ss
    else:
        doc["slot_status_raw"] = out_ss[:8000]
        if err_ss:
            doc["slot_status_stderr"] = err_ss[:2000]

    rc_sl, out_sl, err_sl = _run_cli(
        cli,
        ["slot-list", "--status", "voting"],
        timeout=timeout,
    )
    doc["slots_voting_rc"] = rc_sl
    parsed_sl = _parse_json_maybe(out_sl)
    if isinstance(parsed_sl, list):
        doc["slots_voting_n"] = len(parsed_sl)
        doc["slots_voting"] = parsed_sl[:50]
    else:
        doc["slots_voting_raw"] = out_sl[:8000]
        if err_sl:
            doc["slots_voting_stderr"] = err_sl[:2000]

    rc_ml, out_ml, err_ml = _run_cli(cli, ["market-list"], timeout=timeout)
    doc["market_list_rc"] = rc_ml
    doc["market_list_text"] = out_ml[:12000] if out_ml else ""
    n_m = _market_list_total(out_ml)
    if n_m is not None:
        doc["markets_trading_n"] = n_m
    if err_ml:
        doc["market_list_stderr"] = err_ml[:2000]

    online_hint = "online" in (out_st or "").lower() or "✓" in (out_st or "")
    reachable = parsed_ss is not None or online_hint or rc_ss == 0
    if not reachable and rc_st != 0:
        doc["ok"] = False
        doc["detail"] = "Truthcoin node not reachable (status + slot-status failed); see README for regtest stack"

    if ttl > 0:
        _CACHE = (now, dict(doc))
    return doc
