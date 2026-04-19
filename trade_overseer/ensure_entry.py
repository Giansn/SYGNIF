"""Optional Freqtrade forceenter when flat — gated by signal + dry_run + token."""
from __future__ import annotations

import logging
import os

import config
import ft_client
import orderbook_snapshot
import swarm_snapshot

logger = logging.getLogger("overseer.ensure")


def _instance_by_name(name: str) -> dict | None:
    n = (name or "spot").strip().lower()
    for inst in config.FT_INSTANCES:
        if inst["name"] == n:
            return inst
    return None


def run_ensure_entry(
    *,
    instance_name: str = "spot",
    pair: str = "BTC/USDT",
    ignore_signal: bool = False,
) -> dict:
    """
    If no open trade on ``pair``, POST forceenter (long) when:
      - ``OVERSEER_ENSURE_IGNORE_SIGNAL=1`` or Nautilus sidecar bias is ``long`` (or neutral if IGNORE)
      - live only if ``OVERSEER_ENSURE_LIVE_OK=YES``
    """
    inst = _instance_by_name(instance_name)
    if not inst:
        return {"ok": False, "error": f"unknown instance {instance_name!r}"}

    trades = ft_client.get_open_trades(inst)
    open_same = [t for t in trades if (t.get("pair") or "") == pair]
    if open_same:
        return {
            "ok": True,
            "action": "skip",
            "reason": "already_open",
            "trades": len(open_same),
        }

    if not ignore_signal:
        if os.environ.get("OVERSEER_ENSURE_IGNORE_SIGNAL", "").lower() in ("1", "true", "yes"):
            ignore_signal = True

    if not ignore_signal:
        sig = orderbook_snapshot.load_strategy_signal()
        bias = str((sig or {}).get("bias") or "neutral").lower()
        if bias != "long":
            return {
                "ok": True,
                "action": "skip",
                "reason": f"nautilus_bias_not_long ({bias})",
                "hint": "Set OVERSEER_ENSURE_IGNORE_SIGNAL=1 or pass ignore_signal:true to bypass",
            }

    ok_sw, why_sw = swarm_snapshot.swarm_long_entry_allowed()
    if not ok_sw:
        return {
            "ok": True,
            "action": "skip",
            "reason": why_sw,
            "hint": "Unset OVERSEER_ENSURE_SWARM_GATE or refresh swarm_knowledge_output.json",
        }

    try:
        cfg = ft_client.get_show_config(inst)
    except Exception as e:
        return {"ok": False, "error": f"show_config: {e}"}

    dry = bool(cfg.get("dry_run", True))
    if not dry and os.environ.get("OVERSEER_ENSURE_LIVE_OK", "").strip() != "YES":
        return {
            "ok": False,
            "error": "live bot: set OVERSEER_ENSURE_LIVE_OK=YES to allow forceenter",
        }

    stake_raw = os.environ.get("OVERSEER_ENSURE_STAKE", "50")
    try:
        stake: float | str = float(stake_raw)
    except ValueError:
        stake = stake_raw

    try:
        out = ft_client.forceenter(
            inst,
            pair=pair,
            side="long",
            stake_amount=stake,
            enter_tag="overseer_ensure_entry",
        )
        logger.info("forceenter %s %s -> %s", inst["name"], pair, str(out)[:200])
        return {"ok": True, "action": "forceenter", "result": out}
    except Exception as e:
        logger.exception("forceenter failed")
        return {"ok": False, "error": str(e)}
