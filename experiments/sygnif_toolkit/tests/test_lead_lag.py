import os
import sys
import pandas as pd
import json

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from lead_lag.logic import detect_lead_events
from lead_lag.backtest import run_backtest


def create_synthetic_books():
    import numpy as np

    np.random.seed(42)
    now = int(pd.Timestamp.now().timestamp() * 1000)

    # Generate 1 hour of 100ms data
    timestamps = np.arange(now - 3600 * 1000, now, 100)
    n = len(timestamps)

    # Base price random walk
    base_price = 60000 + np.cumsum(np.random.normal(0, 0.5, n))

    cb_price = base_price + np.random.normal(0, 0.1, n)
    k_price = base_price + np.random.normal(0, 0.1, n)
    b_price = base_price + np.random.normal(0, 0.1, n)

    # Inject a lead event
    lead_idx = n // 2
    cb_price[lead_idx : lead_idx + 10] += 50  # Huge jump
    # Others lag by 300ms (3 ticks)
    k_price[lead_idx + 3 : lead_idx + 13] += 50
    b_price[lead_idx + 3 : lead_idx + 13] += 50

    df_cb = pd.DataFrame({"ts": timestamps, "price": cb_price})
    df_k = pd.DataFrame({"ts": timestamps, "price": k_price})
    df_b = pd.DataFrame({"ts": timestamps, "price": b_price})

    return df_cb, df_k, df_b


def test_lead_lag_end_to_end(capsys):
    df_cb, df_k, df_b = create_synthetic_books()
    events = detect_lead_events(df_k, df_cb, df_b)

    # Write events to fixture
    events_file = "fixtures/events.jsonl"
    with open(events_file, "w") as f:
        for ev in events:
            f.write(json.dumps(ev) + "\n")

    # Use existing fills fixture
    fills_file = "fixtures/fills.jsonl"

    # Just run it so we get the statistical report
    run_backtest(fills_file, events_file)

    captured = capsys.readouterr()
    assert "Cross-Venue Lead-Lag Backtest Report" in captured.out
    assert "CONCLUSION:" in captured.out
