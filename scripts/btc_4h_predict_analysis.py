#!/usr/bin/env python3
"""
4h-Bybit-Kontext (langer Lookback) + Sygnif-TA aus finance_agent/bot.py + kurzer /predict-Stil-Ausblick.

- Daten: Bybit Spot BTCUSDT, Intervall 240 (4h), bis zu 1000 Kerzen (API-Maximum) ≈ ~167 Tage Historie.
- TA: dieselben calc_indicators / calc_ta_score / detect_signals wie der Bot — hier auf dem 4h-DF
  (andere Zeitachse als Live-/ta mit 1h; für Struktur/Makro-Swing, nicht 5m-Entry).
- Prognose-Horizont: realistisch falsifizierbar 24–72h (6–18×4h); länger nur als unscharfer Bias.
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

# Repo root → finance_agent
_REPO = Path(__file__).resolve().parents[1]
_FA = _REPO / "finance_agent"
if _FA.is_dir():
    sys.path.insert(0, str(_FA))


def main() -> int:
    p = argparse.ArgumentParser(description="BTC 4h Sygnif-TA + predict-style outlook")
    p.add_argument("--symbol", default="BTCUSDT", help="Spot symbol, default BTCUSDT")
    p.add_argument("--limit", type=int, default=1000, help="Kline limit (max 1000 on Bybit)")
    p.add_argument("--lang", choices=("de", "en"), default="de")
    args = p.parse_args()
    lim = max(50, min(1000, args.limit))

    import bot as fabot  # noqa: PLC0415

    sym = args.symbol.upper()
    if not sym.endswith("USDT"):
        sym = f"{sym}USDT"

    df = fabot.bybit_kline(sym, interval="240", limit=lim)
    if df.empty or len(df) < 50:
        print("Keine / zu wenige 4h-Daten.", file=sys.stderr)
        return 1

    ind = fabot.calc_indicators(df)
    if not ind:
        print("calc_indicators leer.", file=sys.stderr)
        return 1
    ta = fabot.calc_ta_score(ind)
    sig = fabot.detect_signals(ind, sym.replace("USDT", ""))

    ts_first = int(df["ts"].iloc[0]) / 1000.0
    ts_last = int(df["ts"].iloc[-1]) / 1000.0
    span_h = (ts_last - ts_first) / 3600.0
    span_d = span_h / 24.0

    price = float(ind["price"])
    sup = float(ind.get("support") or 0)
    res = float(ind.get("resistance") or 0)
    rsi = float(ind.get("rsi") or 0)
    trend = str(ind.get("trend", "?"))
    atr_pct = float(ind.get("atr_pct") or 0)

    # Letzte ~30 Tage Range (30*24/4 = 180 Kerzen)
    tail = min(len(df), max(50, int(30 * 24 / 4)))
    seg = df.tail(tail)
    hi30 = float(seg["high"].max())
    lo30 = float(seg["low"].min())

    score = ta.get("score")
    entries = sig.get("entries") or []
    lev = sig.get("leverage")

    de = args.lang == "de"
    if de:
        print("## BTC 4h — Kontext + Kurzprognose (Sygnif-Bot-Logik auf 4h)\n")
        print(f"- **Quelle:** Bybit Spot `{sym}`, **4h**, **{len(df)}** Kerzen (~**{span_d:.0f}** Tage Rückblick, bis API-Limit).")
        print(
            f"- **Zeitspanne:** {datetime.fromtimestamp(ts_first, tz=timezone.utc):%Y-%m-%d %H:%M} UTC → "
            f"{datetime.fromtimestamp(ts_last, tz=timezone.utc):%Y-%m-%d %H:%M} UTC (letzte geschlossene Kerze im Feed je nach Exchange-Schnitt)."
        )
        print(f"- **Letzter Close (Indikator-Ende):** {price:,.2f} USDT")
        print(f"- **~30-Tage-4h-Range:** {lo30:,.2f} – {hi30:,.2f}")
        print(f"- **S/R (Bot-Logik):** Support **{sup:,.2f}** · Resistance **{res:,.2f}**")
        print(f"- **RSI(14) (4h):** {rsi:.1f} · **Trend-Label:** {trend} · **ATR%:** {atr_pct:.2f}")
        print(f"- **Sygnif TA-Score (4h-Serie):** **{score}** · **Entries:** `{entries[:4]}` · **Leverage-Hinweis:** {lev}x")
        print("\n### Realistischer Prognose-Horizont (wie /predict + `prediction_horizon_check`)\n")
        print(
            "| Horizont | Nutzen |\n"
            "|----------|--------|\n"
            "| **24–72 h** (6–18×4h) | **Falsifizierbar:** Kurs bricht S/R oder nicht — passt zu mechanischem Check (`prediction_horizon_check.py`). |\n"
            "| **1–2 Wochen** | Nur **Bias** (Trend/RSI-Richtung), keine Punkt-„Zielkurs“-Garantie. |\n"
            "| **> 1 Monat** | Aus **1000×4h** ableitbar (Struktur), aber **nicht** als präzise Timing-Hypothese; Regime wechseln. |"
        )
        print("\n### Szenarien (nächste 24–72 h, gegen letzten Close)\n")
        mid = price
        print(f"- **Base:** Range zwischen **{min(sup, res):,.2f}** und **{max(sup, res):,.2f}** bleibt relevant; Close hält sich um **{mid:,.2f}**.")
        print(f"- **Up:** Ausbruch **über ~{res:,.2f}** (Resistance) mit Follow-through → Kurzfrist-Bullish invalidiert **unter** letztem Swing-Tief nahe **{sup:,.2f}**.")
        print(f"- **Down:** Break **unter ~{sup:,.2f}** → Bearish/Range-Auflösung nach unten; Aufwärts-Thesis zurück, wenn wieder **über {res:,.2f}**.")
        print(
            f"\n_Kein Trade-Rat; Live-Entries folgen **SygnifStrategy** auf anderen TF/Regeln. "
            "Snapshot + Bewegungsmetriken: `python3 scripts/prediction_horizon_check.py save --symbol BTC --interval 240 --limit 1000` "
            "(oder `--interval 60` für 1h-Default)._\n"
        )
    else:
        print("## BTC 4h context + short outlook\n")
        print(f"- **{sym}** **4h** **{len(df)}** bars (~{span_d:.0f}d). Last close **{price:,.2f}**.")
        print(f"- **S/R:** {sup:,.2f} / {res:,.2f} · **RSI14:** {rsi:.1f} · **Trend:** {trend}")
        print(f"- **Sygnif TA score:** {score} · **entries:** {entries[:4]}")
        print("\n**Horizon:** Falsifiable **24–72h**; longer = bias only.\n")
        print(f"- **Base:** chop around S/R. **Up:** > {res:,.2f}. **Down:** < {sup:,.2f}.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
