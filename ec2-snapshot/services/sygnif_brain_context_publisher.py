"""sygnif_brain_context_publisher.py — structured trading-context feed for the brain.

Every CONTEXT_INTERVAL_SEC (default 60s), aggregate a snapshot from:
  - discovery/latest.json    (regime, IV, GEX, max_pain, rr_25d, OI)
  - microstructure-snapshot  (orderbook imbalance, walls, insurance, funding)
  - portfolio.demo neuron    (equity, open positions, uPnL)
  - swarm.recent forecast    (predict_loop signal + conf)
  - swarm.recent trade.close (last realized outcomes for reward signal)

…and POST a single structured SYGNIF_CONTEXT line to the brain.

Why this matters:
  STDP needs CONSISTENTLY ENCODED inputs that VARY in features so the
  brain can associate input patterns with outcomes. The existing feeders
  emit either sparse trade events (rare) or repetitive sygnif_swarm
  bundles (mostly MIXED/flat). This publisher produces one rich,
  structured line per minute that always has fresh values for regime,
  IV, GEX, max-pain distance, equity drift — the actual features the
  brain should be learning to associate with reward.

  When a trade closes profitably, we ALSO post a SYGNIF_REWARD line
  with executive_boost=high so the brain gets a dopamine pulse it can
  use for STDP credit assignment back to the recent context features.

Env:
  SYGNIF_BRAIN_CONTEXT_INTERVAL  default 60 sec
  SYGNIF_NL_URL                  default http://127.0.0.1:8889
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path("/home/ubuntu/sygnif-agent-mirror")
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

INTERVAL    = int(os.environ.get("SYGNIF_BRAIN_CONTEXT_INTERVAL", "60"))
NL_URL      = os.environ.get("SYGNIF_NL_URL", "http://127.0.0.1:8889").rstrip("/")
SWARM_DB    = "/var/lib/sygnif/swarm.db"
DISCOVERY   = ROOT / "discovery" / "latest.json"
MICRO       = Path.home() / ".sygnif" / "microstructure-snapshot.json"
LOG_PATH    = Path.home() / ".sygnif" / "brain-context.log"
WATERMARK   = Path.home() / ".sygnif" / "brain-context-watermark.json"

LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
# systemd's StandardOutput=append handles file writes; we only stream to stdout.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.StreamHandler()],
)
log = logging.getLogger("brain_context")


# --- helpers ----------------------------------------------------------------
def load_json(p: Path) -> dict:
    try:
        return json.loads(p.read_text())
    except Exception:
        return {}


def post_brain(text: str, *, source: str, executive_boost: float | None = None) -> bool:
    body = {"text": text, "source": source}
    if executive_boost is not None:
        body["executive_boost"] = float(executive_boost)
    payload = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        f"{NL_URL}/api/input/text",
        data=payload, method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return 200 <= resp.status < 300
    except Exception as e:
        log.debug("post failed: %s", e)
        return False


def fetch_portfolio() -> dict:
    try:
        import sygnif_neurons as N
        r = N.run("portfolio.demo", {})
        if r.get("ok"):
            return r.get("data") or {}
    except Exception as e:
        log.debug("portfolio.demo failed: %s", e)
    return {}


def fetch_recent_forecast() -> dict:
    try:
        conn = sqlite3.connect(f"file:{SWARM_DB}?mode=ro", uri=True)
        row = conn.execute(
            "SELECT created, content FROM swarm_entries "
            "WHERE topic='forecast' AND content LIKE '{%' "
            "ORDER BY created DESC LIMIT 1"
        ).fetchone()
        conn.close()
        if not row:
            return {}
        return json.loads(row[1])
    except Exception:
        return {}


def fetch_unrewarded_closes(since_ts: float) -> list[dict]:
    """Pull trade.close rows newer than the watermark for outcome reinforcement."""
    try:
        conn = sqlite3.connect(f"file:{SWARM_DB}?mode=ro", uri=True)
        rows = conn.execute(
            "SELECT created, meta FROM swarm_entries "
            "WHERE topic='trade.close' AND created > ? ORDER BY created ASC LIMIT 20",
            (since_ts,),
        ).fetchall()
        conn.close()
    except Exception:
        return []
    out = []
    for created, meta_str in rows:
        try:
            d = json.loads(meta_str)
            if isinstance(d, dict) and "_unstructured" in d:
                d = json.loads(d["_unstructured"])
            d["_created"] = float(created)
            out.append(d)
        except Exception:
            continue
    return out


def load_watermark() -> float:
    if not WATERMARK.exists():
        return time.time() - 600
    try:
        return float(json.loads(WATERMARK.read_text()).get("ts", time.time() - 600))
    except Exception:
        return time.time() - 600


def save_watermark(ts: float) -> None:
    WATERMARK.parent.mkdir(parents=True, exist_ok=True)
    WATERMARK.write_text(json.dumps({"ts": ts, "saved_at": time.time()}))


# --- the structured context line --------------------------------------------
def build_context_line(disc: dict, micro: dict, port: dict, fc: dict) -> str:
    """Compact, repeatable, always-fresh context. Field order is stable so
    STDP sees the same encoding each cycle, with values that drift over
    time. Format: SPACE-SEPARATED key=value pairs starting with SYGNIF_CONTEXT
    so the brain server treats it as a known sensor stream.
    """
    btc = (disc.get("btc_focus") or {}).get("perp", {}) or {}
    options = disc.get("options", {}) or {}
    regime = (disc.get("regime") or {}).get("label") or "?"
    spot = btc.get("last") or btc.get("mark") or 0
    ch24 = btc.get("ch24h_pct") or 0
    funding_rate = btc.get("fundingRate") or 0
    oi_btc = btc.get("openInterest_btc") or 0

    atm_iv = options.get("atm_iv_nearest") or 0
    iv_rv = options.get("iv_realized_ratio_1h") or 0
    implied_1d = options.get("iv_implied_daily_move_usd") or 0
    max_pain = (options.get("max_pain") or {}).get("strike") or 0
    rr_25d = (options.get("rr_25d") or {}).get("value")
    rr_25d = float(rr_25d) if rr_25d is not None else 0.0
    gex_total = (options.get("gex") or {}).get("total_usd") or 0

    ms = (micro.get("symbols") or {}).get("BTCUSDT", {}) or {}
    imb = float(ms.get("imbalance") or 0)
    spread_bps = float(ms.get("spread_bps") or 0)
    # walls are stored as strings in the snapshot; coerce defensively
    wall_bid = float(ms.get("wall_bid") or 0)
    wall_ask = float(ms.get("wall_ask") or 0)
    ins = micro.get("insurance") or {}
    ins_pool = ins.get("pool_usdt") or 0
    ins_d24 = ins.get("delta_24h_pct") or 0

    eq = port.get("equity_usdc") or 0
    open_n = port.get("open_count") or 0
    upnl = port.get("total_unrealized_usdc") or 0

    fc_regime = ((fc.get("regime") or {}).get("label")) or "?"
    fc_signal = fc.get("signal") or "none"
    fc_action = fc.get("action") or "?"

    pain_dist_pct = ((spot - max_pain) / spot * 100) if (spot and max_pain) else 0

    # 2026-05-10: TA indicator stack (added to discovery via ta_indicators.py)
    klines = (disc.get("btc_focus") or {}).get("klines", {}) or {}
    ta_60 = (klines.get("60") or {}).get("ta", {}) or {}
    ta_240 = (klines.get("240") or {}).get("ta", {}) or {}
    fib = ta_60.get("fib", {}) or {}
    fib_near_label = fib.get("nearest_label", "?")
    fib_near_level = fib.get("nearest_level") or 0
    fib_near_dist = fib.get("nearest_dist_pct") or 0
    rsi_60 = ta_60.get("rsi14") or 0
    rsi_240 = ta_240.get("rsi14") or 0
    macd_60 = (ta_60.get("macd") or {}) or {}
    macd_hist = macd_60.get("hist") or 0
    sr_60 = (ta_60.get("stoch_rsi") or {}) or {}
    sr_k = sr_60.get("k") or 0
    sr_regime = sr_60.get("regime", "?")
    cmf_60 = ta_60.get("cmf20") or 0
    mfi_60 = ta_60.get("mfi14") or 0

    return (
        f"SYGNIF_CONTEXT "
        f"regime={regime} predict={fc_regime} action={fc_action} signal={fc_signal} "
        f"spot={spot:.0f} ch24h={ch24:+.2f}% "
        f"iv={atm_iv:.3f} iv_rv={iv_rv:.2f} implied1d=${implied_1d:.0f} "
        f"max_pain={max_pain:.0f} pain_dist={pain_dist_pct:+.2f}% "
        f"rr25d={rr_25d:+.4f} gex=${gex_total:+.0f} "
        f"oi_btc={oi_btc:.0f} funding={funding_rate*1e6:+.0f}ppm "
        f"imb={imb:+.2f} spread={spread_bps:.2f}bps "
        f"wall_bid={wall_bid:.0f} wall_ask={wall_ask:.0f} "
        f"ins_pool=${ins_pool:.0f} ins_d24={ins_d24:+.2f}% "
        f"equity=${eq:.0f} open={open_n} upnl=${upnl:+.2f} "
        f"rsi60={rsi_60:.0f} rsi240={rsi_240:.0f} macdh={macd_hist:+.1f} "
        f"stochk={sr_k:.0f}({sr_regime}) cmf={cmf_60:+.3f} mfi={mfi_60:.0f} "
        f"fib_near={fib_near_label}@${fib_near_level:.0f}({fib_near_dist:+.2f}%)"
    )


def build_reward_line(close_meta: dict) -> tuple[str, float] | None:
    """Build a SYGNIF_REWARD line + executive_boost magnitude for a closed trade.

    closed_pnl > 0 → reward_boost +0.5..+1.0 (capped)
    closed_pnl < 0 → punishment_boost -0.5..-1.0
    closed_pnl == 0 / None → skip (no signal)
    """
    cp = close_meta.get("closed_pnl") or close_meta.get("closedPnl")
    if cp is None:
        return None
    try:
        pnl = float(cp)
    except Exception:
        return None
    if pnl == 0:
        return None
    sym = close_meta.get("symbol", "?")
    side = close_meta.get("side", "?")
    qty = close_meta.get("exec_qty") or close_meta.get("closed_size") or "?"
    instrument = close_meta.get("instrument", "?")
    win = pnl > 0
    # Magnitude: scale by abs($pnl) with cap
    boost = max(-1.0, min(1.0, pnl / 5.0))  # $5 PnL → ±1.0 boost
    line = (f"SYGNIF_REWARD {'WIN' if win else 'LOSS'} {sym} {side} qty={qty} "
            f"instrument={instrument} pnl_usdc=${pnl:+.3f} boost={boost:+.2f}")
    return line, boost


# --- main loop --------------------------------------------------------------
def cycle() -> None:
    disc = load_json(DISCOVERY)
    micro = load_json(MICRO)
    port = fetch_portfolio()
    fc = fetch_recent_forecast()

    # 1) regular context line (every cycle)
    line = build_context_line(disc, micro, port, fc)
    ok = post_brain(line, source="brain_context")
    log.info("CONTEXT  ok=%s len=%d", ok, len(line))

    # 2) reward lines for any newly-closed trades since watermark
    wm = load_watermark()
    closes = fetch_unrewarded_closes(wm)
    new_wm = wm
    for c in closes:
        new_wm = max(new_wm, c.get("_created", wm))
        rb = build_reward_line(c)
        if rb is None:
            continue
        rline, boost = rb
        ok = post_brain(rline, source="brain_reward", executive_boost=boost)
        log.info("REWARD   %s pnl=%.3f boost=%+.2f ok=%s", c.get("symbol"),
                 float(c.get("closed_pnl") or 0), boost, ok)
        # 2026-05-10: also nudge SalienceClassifier toward the outcome label.
        # Closes the STDP-correlation learning loop — over many trades, the
        # 80-neuron salience head learns to predict win/loss from ambient state.
        try:
            pnl = float(c.get("closed_pnl") or 0)
            outcome = "win" if pnl > 0 else "loss"
            req = urllib.request.Request(
                f"{NL_URL}/api/brain/salience/refine",
                data=json.dumps({"outcome": outcome, "lr": 0.05}).encode(),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                if 200 <= resp.status < 300:
                    log.info("REFINE   outcome=%s ok=True", outcome)
        except Exception as _e:
            log.debug("salience refine failed: %s", _e)
    if new_wm > wm:
        save_watermark(new_wm)


def main() -> None:
    log.info("brain_context_publisher started (interval=%ds)", INTERVAL)
    while True:
        try:
            cycle()
        except Exception:
            log.exception("cycle crashed")
        time.sleep(INTERVAL)


if __name__ == "__main__":
    if "--once" in sys.argv:
        cycle()
    else:
        main()
