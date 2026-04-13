#!/usr/bin/env python3
"""
Research demo: breakout + combinatorial-style purged splits + PSR (Quant Beckman flow).

Mirrors the *structure* of:
  https://www.quantbeckman.com/p/with-code-combinatorial-purged-cross
Synthetic series by default; optional `--json` = Bybit-style OHLCV list (`c` close).

Dependencies: numpy only.
"""
from __future__ import annotations

import argparse
import json
import math
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import numpy as np

# ---------------------------------------------------------------------------
# 1) Breakout + signal propagation (article-style)
# ---------------------------------------------------------------------------


class Breakout:
    """NumPy breakout on a 1D price series."""

    def __init__(self, window: int = 20, direction: str = "up") -> None:
        self.window = int(window)
        if self.window <= 0:
            raise ValueError("window must be positive")
        if direction not in ("up", "down"):
            raise ValueError("direction must be 'up' or 'down'")
        self.direction = direction

    def predict(self, data: np.ndarray) -> np.ndarray:
        data = np.asarray(data, dtype=float)
        n = len(data)
        signals = np.zeros(n, dtype=int)
        for i in range(self.window, n):
            w = data[i - self.window : i]
            if self.direction == "up":
                if data[i] > np.max(w):
                    signals[i] = -1
            else:
                if data[i] < np.min(w):
                    signals[i] = 1
        return signals


def propagate_ones(input_array: np.ndarray, n_prop: int) -> np.ndarray:
    result = np.array(input_array, dtype=int)
    for _ in range(n_prop):
        shifted = np.roll(result, -1)
        can_propagate = (result == 1) & (shifted == 0)
        result[np.where(np.roll(can_propagate, 1))] = 1
    return result


def model_signal(series: np.ndarray, n_window: int, signal_propagation: int = 0) -> np.ndarray:
    risk_on = Breakout(window=n_window, direction="up")
    risk_off = Breakout(window=n_window, direction="down")
    signals_sell = risk_on.predict(series)
    signals_buy = risk_off.predict(series)
    _ = signals_sell  # long-only demo (article leaves short commented)
    signals = propagate_ones(np.where(signals_buy == 1, 1, 0), signal_propagation)
    return signals.astype(float)


# ---------------------------------------------------------------------------
# 2) Backtest + moments + PSR
# ---------------------------------------------------------------------------


def backtesting(
    price: np.ndarray,
    signals: np.ndarray,
    shift: int = 1,
    commissions: float = 0.0,
) -> np.ndarray:
    price = np.asarray(price, dtype=float)
    signals = np.asarray(signals, dtype=float)
    signals_shifted = np.roll(signals, shift)
    signals_shifted[:shift] = 0.0
    price_diff = np.diff(price, prepend=price[0])
    strategy_returns = signals_shifted * price_diff
    trades = np.diff(signals_shifted, prepend=0.0)
    commission_costs = np.abs(trades) * commissions
    net_returns = strategy_returns - commission_costs
    pnl = np.cumsum(net_returns)
    return pnl if pnl.size > 0 else np.array([0.0], dtype=float)


def skew_numpy(x: np.ndarray) -> float:
    x = np.asarray(x, dtype=float)
    if len(x) < 3:
        return 0.0
    mu, sigma = float(np.mean(x)), float(np.std(x))
    if sigma < 1e-12:
        return 0.0
    return float(np.mean(((x - mu) / sigma) ** 3))


def kurtosis_numpy(x: np.ndarray) -> float:
    x = np.asarray(x, dtype=float)
    if len(x) < 4:
        return 0.0
    mu, sigma = float(np.mean(x)), float(np.std(x))
    if sigma < 1e-12:
        return 0.0
    return float(np.mean(((x - mu) / sigma) ** 4))


def norm_cdf_numpy(x: float) -> float:
    return float(0.5 * (1.0 + math.erf(x / math.sqrt(2.0))))


def calculate_probabilistic_sharpe_ratio(pnl: np.ndarray, benchmark_sr_anual: float = 1.0) -> float:
    daily_returns = np.diff(pnl, prepend=float(pnl[0]) if pnl.size else 0.0)
    n = len(daily_returns)
    if n < 20:
        return 0.0
    mu, sigma = float(np.mean(daily_returns)), float(np.std(daily_returns, ddof=1))
    if sigma < 1e-12:
        return 1.0 if mu > 0 else 0.0
    sk = skew_numpy(daily_returns)
    kurt = kurtosis_numpy(daily_returns)
    sr = mu / sigma * math.sqrt(252.0)
    psr_num = (sr - benchmark_sr_anual) * math.sqrt(n - 1)
    psr_den_sq = 1.0 - sk * sr + ((kurt - 1.0) / 4.0) * sr**2
    if psr_den_sq <= 0 or math.isnan(psr_den_sq):
        return 1.0 if psr_num > 0 else 0.0
    z_stat = psr_num / math.sqrt(psr_den_sq)
    return norm_cdf_numpy(z_stat)


# ---------------------------------------------------------------------------
# 3) Purged combinatorial-style splitter + robust N
# ---------------------------------------------------------------------------


class CombinatorialPurgedSplit:
    """Random train/test windows with a purge gap (chronological, article-style demo)."""

    def __init__(
        self,
        n_splits: int = 100,
        train_size_pct: float = 0.7,
        test_size_pct: float = 0.1,
        purge_size: int = 10,
    ) -> None:
        self.n_splits = int(n_splits)
        self.train_size_pct = float(train_size_pct)
        self.test_size_pct = float(test_size_pct)
        self.purge_size = int(purge_size)

    def split(self, X: np.ndarray) -> Iterator[tuple[np.ndarray, np.ndarray]]:
        n_samples = len(X)
        train_len = int(n_samples * self.train_size_pct)
        test_len = int(n_samples * self.test_size_pct)
        max_start = n_samples - train_len - self.purge_size - test_len
        if max_start <= 0:
            raise ValueError(
                f"Series too short for train={train_len} purge={self.purge_size} test={test_len} (n={n_samples})"
            )
        for _ in range(self.n_splits):
            train_start = int(np.random.randint(0, max_start + 1))
            train_end = train_start + train_len
            test_start = train_end + self.purge_size
            test_end = test_start + test_len
            yield np.arange(train_start, train_end), np.arange(test_start, test_end)


def get_robust_n_from_top_candidates(top_results: list[dict[str, Any]]) -> int:
    if not top_results:
        raise ValueError("empty top_results")
    best_ns = [int(res["N"]) for res in top_results]
    weights_raw = np.array([float(res["psr_10th_percentile"]) for res in top_results], dtype=float)
    min_metric = float(np.min(weights_raw))
    weights = weights_raw - min_metric
    if float(np.sum(weights)) < 1e-9:
        return int(np.round(np.median(best_ns)))
    weighted_ns_list: list[int] = []
    for n_value, weight in zip(best_ns, weights):
        repeat_count = int(np.round(weight * 1000))
        weighted_ns_list.extend([n_value] * max(repeat_count, 1))
    if not weighted_ns_list:
        return int(np.round(np.median(best_ns)))
    return int(np.round(np.median(weighted_ns_list)))


def find_robust_parameter_combinatorial(
    price: np.ndarray,
    data_series: np.ndarray,
    model_function: Callable[[np.ndarray, int], np.ndarray],
    n_range: tuple[int, int],
    step: int = 1,
    n_search_paths: int = 200,
    purge_size: int = 10,
    benchmark_sr: float = 0.5,
) -> tuple[int, list[dict[str, Any]]]:
    np.random.seed(42)
    cv_splitter = CombinatorialPurgedSplit(n_splits=n_search_paths, purge_size=purge_size)
    candidate_ns = list(range(int(n_range[0]), int(n_range[1]) + 1, int(step)))
    psr_distributions: dict[int, list[float]] = {N: [] for N in candidate_ns}

    print(f"Starting combinatorial-style search across {n_search_paths} paths...")
    for i, (train_idx, test_idx) in enumerate(cv_splitter.split(data_series)):
        _ = train_idx  # article evaluates on test only (no fit)
        if (i + 1) % 50 == 0:
            print(f"  path {i + 1}/{n_search_paths}")
        test_data = data_series[test_idx]
        test_price = price[test_idx]
        for N in candidate_ns:
            if len(test_data) <= N:
                continue
            test_signals = model_function(test_data, N)
            pnl = backtesting(test_price, test_signals)
            psr = calculate_probabilistic_sharpe_ratio(pnl, benchmark_sr)
            psr_distributions[N].append(psr)

    results: list[dict[str, Any]] = []
    for N, scores in psr_distributions.items():
        if not scores:
            continue
        scores_np = np.asarray(scores, dtype=float)
        results.append(
            {
                "N": N,
                "psr_median": float(np.median(scores_np)),
                "psr_mean": float(np.mean(scores_np)),
                "psr_std": float(np.std(scores_np)),
                "psr_10th_percentile": float(np.percentile(scores_np, 10)),
            }
        )
    if not results:
        raise ValueError("No PSR scores collected (increase series length or shrink N_range).")

    sorted_results = sorted(results, key=lambda r: r["psr_10th_percentile"], reverse=True)
    top_5 = sorted_results[:5]
    print("\n--- Top 5 by PSR 10th percentile ---")
    print(f"{'N':<5} | {'PSR med':<10} | {'PSR 10%':<10}")
    print("-" * 32)
    for res in top_5:
        print(f"{res['N']:<5} | {res['psr_median']:<10.3f} | {res['psr_10th_percentile']:<10.3f}")

    best_n = get_robust_n_from_top_candidates(top_5)
    print(f"\nWeighted-median N from top-5: {best_n}")
    return best_n, results


def model_signal_adapter(series: np.ndarray, n_window: int, propagation: int = 0) -> np.ndarray:
    return model_signal(series, n_window, signal_propagation=propagation)


# ---------------------------------------------------------------------------
# 4) Synthetic data (compact stand-in for the article’s generator)
# ---------------------------------------------------------------------------


def generate_synthetic_index_and_factor(n: int = 3000, seed: int = 2025) -> tuple[np.ndarray, np.ndarray]:
    rng = np.random.default_rng(seed)
    factor = np.empty(n, dtype=float)
    factor[0] = 20.0
    for t in range(1, n):
        factor[t] = 0.95 * factor[t - 1] + 0.05 * 20.0 + rng.normal(0.0, 0.35)
    noise = rng.normal(0.0, 0.012, size=n)
    drift = -0.00015 * np.diff(factor, prepend=factor[0])
    logp = np.cumsum(drift + noise)
    index_open = 100.0 * np.exp(logp)
    return index_open, factor


def load_bybit_json_closes(path: Path) -> np.ndarray:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw, dict) and "daily" in raw:
        raise SystemExit("Pass a raw OHLCV list JSON (e.g. btc_daily_ohlcv_long.json), not macro panel dict.")
    rows = raw
    if not isinstance(rows, list) or not rows:
        raise SystemExit("JSON must be a non-empty list of candles")
    key = "c" if "c" in rows[0] else "Close"
    return np.array([float(r[key]) for r in rows], dtype=float)


# ---------------------------------------------------------------------------
# 5) CLI
# ---------------------------------------------------------------------------


def main() -> int:
    ap = argparse.ArgumentParser(description="CPCV-style breakout demo (research).")
    ap.add_argument("--json", type=Path, default=None, help="Bybit-style OHLCV JSON list (uses close).")
    ap.add_argument("--n-min", type=int, default=10)
    ap.add_argument("--n-max", type=int, default=80)
    ap.add_argument("--n-step", type=int, default=5)
    ap.add_argument("--paths", type=int, default=200, help="Number of purged random splits.")
    ap.add_argument("--purge", type=int, default=10, help="Purge bars between train and test blocks.")
    ap.add_argument("--bench-sr", type=float, default=0.5, help="Annual SR benchmark for PSR.")
    ap.add_argument("--propagate", type=int, default=0, help="Signal propagation bars (article optional).")
    args = ap.parse_args()

    if args.json is not None:
        price = load_bybit_json_closes(args.json)
        factor = price.copy()
        print(f"Loaded {len(price)} closes from {args.json}")
        if len(price) < 500:
            print("Warning: short series — CPCV distributions will be noisy.")
    else:
        price, factor = generate_synthetic_index_and_factor(n=3000, seed=2025)
        print(f"Synthetic series n={len(price)}")

    prop = max(0, int(args.propagate))

    def mf(series: np.ndarray, n_window: int) -> np.ndarray:
        return model_signal_adapter(series, n_window, prop)

    best_n, _all = find_robust_parameter_combinatorial(
        price=price,
        data_series=factor,
        model_function=mf,
        n_range=(args.n_min, args.n_max),
        step=args.n_step,
        n_search_paths=args.paths,
        purge_size=args.purge,
        benchmark_sr=args.bench_sr,
    )
    print("\nDone. (Research only — not SygnifStrategy.)")
    _ = best_n
    _ = _all
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
