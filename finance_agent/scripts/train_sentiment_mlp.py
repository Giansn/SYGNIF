#!/usr/bin/env python3
"""
Train numpy sentiment MLP on synthetic + optional Freqtrade closed-trade rows.

Teacher labels = rule-based score_from_signals (same logic as production expert).
Closed trades → headline/live text via closed_trades_reader.trade_to_training_sample
(PnL, exit_reason, enter_tag). Analyze DBs with scripts/analyze_closed_trades.py.

Usage:
  cd /path/to/SYGNIF/finance_agent && python3 scripts/train_sentiment_mlp.py
  python3 scripts/train_sentiment_mlp.py --samples 8000 \\
    --freqtrade-db ../user_data/tradesv3-futures.sqlite --trades-days 90 --print-analysis
"""

from __future__ import annotations

import argparse
import random
import sys
from pathlib import Path

import numpy as np

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from closed_trades_analysis import analyze_closed_trades, format_analysis_text
from closed_trades_reader import fetch_closed_trades, trade_to_training_sample
from expert_sentiment import aggregate_sentiment_signals, score_from_signals
from sentiment_mlp import FEATURE_DIM, MLPWeights, save_weights, signals_to_feature_matrix

TOKENS = ("btc", "eth", "sol", "xrp", "doge", "ada")

# Phrase-rich templates → keyword hits similar to real RSS/live tape
_BULL_HEADLINES = [
    "{tok} bullish breakout as institutional buying returns",
    "ETF inflows surge; rally extends with accumulation",
    "{tok} gains on partnership and mainnet upgrade news",
    "Bullish surge: adoption milestone and record high chatter",
    "Uptrend holds — rebound after recovery narrative",
]

_BEAR_HEADLINES = [
    "Exchange hack reported; selloff and bearish tone spread",
    "SEC investigation fuels crash fears; delisting rumors",
    "Liquidation cascade after plunge; withdrawals paused",
    "Bearish dump amid lawsuit and fraud warnings",
    "Security breach triggers outflows and warning from analysts",
]

_NEUTRAL_HEADLINES = [
    "Volumes light ahead of macro data; range trade",
    "Mixed tape: no clear catalyst for {tok}",
    "Traders await clarity; headline flow quiet",
]

_LIVE_BULL = [
    "{tok} spot bid lifting, tape bullish, rally in perps",
    "Funding stable, inflows visible on tape, breakout attempt",
]

_LIVE_BEAR = [
    "Offer heavy, selloff in perps, liquidation prints",
    "Bearish skew: crash hedges, outflows on tape",
]

_LIVE_NEUTRAL = [
    "Book balanced, spread tight, no clear impulse on {tok}",
    "Microstructure quiet: mid holds, range-bound tape",
]


def _fmt(s: str, tok: str) -> str:
    return s.replace("{tok}", tok.upper()).replace("{TOK}", tok.upper())


def _random_headline_mix(rng: random.Random, tok: str) -> list[str]:
    k = rng.randint(0, 8)
    lines: list[str] = []
    for _ in range(k):
        pool = rng.random()
        if pool < 0.38:
            lines.append(_fmt(rng.choice(_BULL_HEADLINES), tok))
        elif pool < 0.76:
            lines.append(_fmt(rng.choice(_BEAR_HEADLINES), tok))
        else:
            lines.append(_fmt(rng.choice(_NEUTRAL_HEADLINES), tok))
    return lines


def _maybe_live(rng: random.Random, tok: str) -> str:
    if rng.random() > 0.55:
        return ""
    pool = rng.random()
    if pool < 0.35:
        return _fmt(rng.choice(_LIVE_BULL), tok)
    if pool < 0.7:
        return _fmt(rng.choice(_LIVE_BEAR), tok)
    return _fmt(rng.choice(_LIVE_NEUTRAL), tok)


def row_from_trade(trade: dict) -> tuple[np.ndarray, float]:
    tok, headlines, live, ta = trade_to_training_sample(trade)
    sig = aggregate_sentiment_signals(tok, headlines, live)
    y, _ = score_from_signals(sig, ta)
    x = signals_to_feature_matrix(sig, ta)
    return x, float(y)


def generate_row(rng: random.Random) -> tuple[np.ndarray, float]:
    tok = rng.choice(TOKENS)
    ta = rng.uniform(18.0, 82.0)
    headlines = _random_headline_mix(rng, tok)
    live = _maybe_live(rng, tok)
    sig = aggregate_sentiment_signals(tok, headlines, live)
    y, _ = score_from_signals(sig, ta)
    x = signals_to_feature_matrix(sig, ta)
    return x, float(y)


def train(
    X: np.ndarray,
    y: np.ndarray,
    *,
    epochs: int = 500,
    lr: float = 0.04,
    batch: int = 256,
    seed: int = 42,
) -> MLPWeights:
    rng = np.random.default_rng(seed)
    n, d_in = X.shape
    assert d_in == FEATURE_DIM
    h1, h2 = 24, 12

    mean = X.mean(axis=0)
    std = X.std(axis=0) + 1e-6
    Xn = (X - mean) / std

    W1 = rng.normal(0, 0.2, (d_in, h1))
    b1 = np.zeros(h1)
    W2 = rng.normal(0, 0.2, (h1, h2))
    b2 = np.zeros(h2)
    W3 = rng.normal(0, 0.15, (h2,))
    b3 = np.zeros(1)

    idx = np.arange(n)
    for ep in range(epochs):
        rng.shuffle(idx)
        total_loss = 0.0
        for start in range(0, n, batch):
            sl = idx[start : start + batch]
            B = len(sl)
            if B == 0:
                continue
            xb = Xn[sl]
            yb = y[sl]

            z1 = xb @ W1 + b1
            a1 = np.maximum(0.0, z1)
            z2 = a1 @ W2 + b2
            a2 = np.maximum(0.0, z2)
            y_hat = a2 @ W3 + b3[0]

            loss = 0.5 * np.mean((y_hat - yb) ** 2)
            total_loss += loss * B

            d_yhat = (y_hat - yb) / B
            db3_scalar = np.sum(d_yhat)
            dW3 = a2.T @ d_yhat
            da2 = d_yhat[:, np.newaxis] * W3[np.newaxis, :]
            dz2 = da2 * (z2 > 0)
            dW2 = a1.T @ dz2
            db2 = np.sum(dz2, axis=0)
            da1 = dz2 @ W2.T
            dz1 = da1 * (z1 > 0)
            dW1 = xb.T @ dz1
            db1 = np.sum(dz1, axis=0)

            W1 -= lr * dW1
            b1 -= lr * db1
            W2 -= lr * dW2
            b2 -= lr * db2
            W3 -= lr * dW3
            b3[0] -= lr * db3_scalar

        if (ep + 1) % 100 == 0 or ep == 0:
            print(f"epoch {ep + 1}/{epochs} batch_mse_sum {total_loss / n:.6f}")

    return MLPWeights(
        w1=W1,
        b1=b1,
        w2=W2,
        b2=b2,
        w3=W3,
        b3=b3,
        mean=mean,
        std=std,
    )


def main() -> None:
    ap = argparse.ArgumentParser(description="Train sentiment MLP on synthetic market/headline data.")
    ap.add_argument("--samples", type=int, default=10000, help="Synthetic rows")
    ap.add_argument("--epochs", type=int, default=500)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument(
        "--freqtrade-db",
        action="append",
        default=[],
        dest="freqtrade_dbs",
        help="Freqtrade tradesv3*.sqlite path (repeatable); closed trades augment training",
    )
    ap.add_argument(
        "--trades-days",
        type=int,
        default=0,
        help="Only trades closed in the last N days (0 = all closed trades in DB)",
    )
    ap.add_argument(
        "--trade-oversample",
        type=int,
        default=3,
        help="Repeat each closed-trade row this many times (balances small DBs)",
    )
    ap.add_argument(
        "--print-analysis",
        action="store_true",
        help="Print closed-trade analysis (win rate, by pair, exit families) before training",
    )
    ap.add_argument(
        "--out",
        type=str,
        default="",
        help="Output .npz path (default: SYGNIF user_data/finance_agent/sentiment_mlp.npz)",
    )
    args = ap.parse_args()

    # _ROOT = .../SYGNIF/finance_agent when scripts live under SYGNIF
    default_out = _ROOT.parent / "user_data" / "finance_agent" / "sentiment_mlp.npz"
    out_path = Path(args.out) if args.out else default_out
    out_path.parent.mkdir(parents=True, exist_ok=True)

    trade_days = args.trades_days if args.trades_days > 0 else None
    db_trades: list = []
    for dbp in args.freqtrade_dbs:
        p = Path(dbp)
        got = fetch_closed_trades(p, days=trade_days)
        print(f"# trades from {p}: {len(got)} closed")
        db_trades.extend(got)

    if args.print_analysis and db_trades:
        summary = analyze_closed_trades(db_trades)
        print(format_analysis_text(summary))
    elif args.print_analysis and args.freqtrade_dbs:
        print("(No closed trades matched filters — analysis skipped.)")

    rng_py = random.Random(args.seed)
    rows_x: list[np.ndarray] = []
    rows_y: list[float] = []
    for _ in range(args.samples):
        x, y = generate_row(rng_py)
        rows_x.append(x)
        rows_y.append(y)

    over = max(1, int(args.trade_oversample))
    trade_added = 0
    for t in db_trades:
        for _ in range(over):
            x, y = row_from_trade(t)
            rows_x.append(x)
            rows_y.append(y)
            trade_added += 1

    X = np.stack(rows_x, axis=0)
    y = np.asarray(rows_y, dtype=np.float64)

    print(
        f"Training on {args.samples} synthetic + {trade_added} trade-derived rows "
        f"(total {len(y)}) → {out_path}"
    )
    m = train(X, y, epochs=args.epochs, seed=args.seed)
    save_weights(str(out_path), m)
    print("Saved.", out_path.stat().st_size, "bytes")


if __name__ == "__main__":
    main()
