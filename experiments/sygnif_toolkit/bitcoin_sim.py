import pandas as pd
import numpy as np
import json
import os
from datetime import datetime


def simulate_btc_price(days=60, start_price=60000, volatility=0.04, dt=1.0):
    """Simulates BTC price using Geometric Brownian Motion."""
    np.random.seed(42)  # For reproducibility
    n_steps = int(days / dt)
    prices = np.zeros(n_steps)
    prices[0] = start_price

    for t in range(1, n_steps):
        # random shock
        z = np.random.normal(0, 1)
        # GBM formula
        prices[t] = prices[t - 1] * np.exp(
            -0.5 * volatility**2 * dt + volatility * np.sqrt(dt) * z
        )

    dates = pd.date_range(end=datetime.now(), periods=n_steps, freq="D")
    return pd.DataFrame({"Date": dates, "Price": prices}).set_index("Date")


def calculate_mas(df):
    """Calculates 7-day and 30-day moving averages."""
    df["MA7"] = df["Price"].rolling(window=7).mean()
    df["MA30"] = df["Price"].rolling(window=30).mean()
    return df


def run_golden_cross_strategy(df):
    """Implements a simple Golden Cross strategy."""
    portfolio = {"cash": 100000, "btc": 0}
    ledger = []

    df["Signal"] = 0.0
    # Create signals where MA7 crosses MA30
    df.loc[df["MA7"] > df["MA30"], "Signal"] = 1.0
    df["Position"] = df["Signal"].diff()

    for date, row in df.iterrows():
        if pd.isna(row["Position"]):
            continue

        price = row["Price"]

        # Buy Signal
        if row["Position"] == 1.0 and portfolio["cash"] > 0:
            btc_to_buy = portfolio["cash"] / price
            portfolio["btc"] += btc_to_buy
            portfolio["cash"] = 0
            ledger.append(
                {
                    "Date": date,
                    "Action": "BUY",
                    "Price": price,
                    "BTC": btc_to_buy,
                    "Portfolio Value": portfolio["btc"] * price,
                }
            )
            print(f"{date.date()} | BUY  | Price: ${price:.2f} | BTC: {btc_to_buy:.4f}")

        # Sell Signal
        elif row["Position"] == -1.0 and portfolio["btc"] > 0:
            cash_from_sell = portfolio["btc"] * price
            portfolio["cash"] += cash_from_sell
            btc_sold = portfolio["btc"]
            portfolio["btc"] = 0
            ledger.append(
                {
                    "Date": date,
                    "Action": "SELL",
                    "Price": price,
                    "BTC": btc_sold,
                    "Portfolio Value": portfolio["cash"],
                }
            )
            print(f"{date.date()} | SELL | Price: ${price:.2f} | BTC: {btc_sold:.4f}")

    # Final evaluation
    final_price = df.iloc[-1]["Price"]
    final_value = portfolio["cash"] + portfolio["btc"] * final_price
    print(f"\nFinal Portfolio Value: ${final_value:.2f}")
    print(f"Return: {((final_value - 100000) / 100000) * 100:.2f}%")

    return ledger


def generate_fills_fixture(filename="fixtures/fills.jsonl", num_trades=200):
    """Generates synthetic fills data with known components for edge attribution testing."""
    os.makedirs(os.path.dirname(filename), exist_ok=True)
    np.random.seed(42)

    now = int(datetime.now().timestamp() * 1000)

    fills = []
    for i in range(num_trades):
        # Randomize trade attributes
        side = int(np.random.choice([1, -1]))  # 1 for buy, -1 for sell
        is_maker = bool(np.random.choice([True, False]))
        qty = float(round(np.random.uniform(0.1, 2.0), 4))

        intended_price = float(round(np.random.uniform(50000, 70000), 2))
        intended_ts = int(now - (num_trades - i) * 60000)  # Spread trades over time

        # Simulate slippage
        # Takers typically have adverse slippage, makers might have zero or slight positive
        slippage_bps = (
            np.random.normal(2, 1) if not is_maker else np.random.normal(0, 0.5)
        )
        price_diff = intended_price * (slippage_bps / 10000)

        actual_price = float(
            intended_price + (price_diff * side)
        )  # Buy higher, sell lower = adverse

        # Fee simulation (e.g., 0.05% taker, 0.01% maker)
        fee_rate = 0.0005 if not is_maker else 0.0001
        fee_usd = float(actual_price * qty * fee_rate)

        # Adverse selection (did mid move against us in next 5s?)
        mid_5s_after = float(actual_price + np.random.normal(0, 5) * side)

        fill = {
            "ts": int(
                intended_ts + np.random.randint(50, 200)
            ),  # Actual fill slightly after intent
            "side": side,
            "price": round(actual_price, 2),
            "qty_btc": qty,
            "fee_usd": round(fee_usd, 2),
            "order_type": "maker" if is_maker else "taker",
            "strategy": "sygFAST_sim",
            "intended_price": intended_price,
            "intended_ts": intended_ts,
            # Ground truth data injected for tests to assert against
            "_ground_truth": {
                "mid_5s_after_entry": round(mid_5s_after, 2),
                "funding_rate": round(
                    np.random.normal(0.0001, 0.00005), 6
                ),  # Fake funding
            },
        }
        fills.append(fill)

    with open(filename, "w") as f:
        for fill in fills:
            f.write(json.dumps(fill) + "\n")

    print(f"\nGenerated {num_trades} synthetic fills at {filename}")


if __name__ == "__main__":
    print("--- 60-Day BTC Price Simulation ---")
    df = simulate_btc_price(days=120)  # Need more days to prime the 30-day MA
    df = calculate_mas(df)

    # We only care about the last 60 days for the simulation output
    df_60 = df.iloc[-60:].copy()

    run_golden_cross_strategy(df_60)

    generate_fills_fixture()
