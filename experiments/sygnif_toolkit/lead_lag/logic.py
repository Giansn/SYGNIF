import json
import pandas as pd
import numpy as np

def compute_mid_velocity(df_book, window_ms=2000, lag_ms=500):
    # Sort by timestamp
    df_book = df_book.sort_values('ts').copy()

    # Calculate mid
    if 'bid' in df_book.columns and 'ask' in df_book.columns:
        df_book['mid'] = (df_book['bid'] + df_book['ask']) / 2.0
    else:
        df_book['mid'] = df_book['price']  # Fallback for simplicity if just price is provided

    df_book.set_index(pd.to_datetime(df_book['ts'], unit='ms'), inplace=True)

    # Mid 500ms ago using pandas shift or lookback
    # Since WS books are unevenly spaced, we need to interpolate or use rolling
    # Let's resample to 100ms grid
    df_grid = df_book.resample('100ms').last().ffill()

    # lag_ms = 500ms = 5 periods of 100ms
    periods = lag_ms // 100
    df_grid['mid_t_500'] = df_grid['mid'].shift(periods)

    df_grid['mid_velocity'] = (df_grid['mid'] - df_grid['mid_t_500']) / df_grid['mid_t_500']

    # EWMA over 2s window = 20 periods
    df_grid['mid_velocity_ewma'] = df_grid['mid_velocity'].ewm(span=20).mean()

    return df_grid

def detect_lead_events(df_kraken, df_cb, df_bybit):
    k_grid = compute_mid_velocity(df_kraken)
    c_grid = compute_mid_velocity(df_cb)
    b_grid = compute_mid_velocity(df_bybit)

    # Merge grids
    merged = pd.DataFrame({
        'kraken_vel': k_grid['mid_velocity_ewma'],
        'cb_vel': c_grid['mid_velocity_ewma'],
        'bybit_vel': b_grid['mid_velocity_ewma']
    }).dropna()

    # Calculate 30-min rolling stdev for each
    # 30 min = 30 * 60 * 10 = 18000 periods of 100ms
    window_30m = 18000

    merged['kraken_std'] = merged['kraken_vel'].rolling(min_periods=100, window=window_30m).std()
    merged['cb_std'] = merged['cb_vel'].rolling(min_periods=100, window=window_30m).std()
    merged['bybit_std'] = merged['bybit_vel'].rolling(min_periods=100, window=window_30m).std()

    merged.dropna(inplace=True)

    events = []

    for ts, row in merged.iterrows():
        # Check for lead event where ONE venue exceeds 2 sigma, others < 0.5 sigma same-dir
        venues = ['kraken', 'cb', 'bybit']
        for leader in venues:
            vel = row[f"{leader}_vel"]
            std = row[f"{leader}_std"]

            if abs(vel) > 2 * std:
                # Potential lead
                direction = "up" if vel > 0 else "down"

                followers = [v for v in venues if v != leader]

                followers_quiet = True
                for follower in followers:
                    f_vel = row[f"{follower}_vel"]
                    f_std = row[f"{follower}_std"]

                    # same direction
                    if (direction == "up" and f_vel > 0.5 * f_std) or \
                       (direction == "down" and f_vel < -0.5 * f_std):
                        followers_quiet = False
                        break

                if followers_quiet:
                    events.append({
                        "ts": int(ts.timestamp() * 1000),
                        "kind": "lead_event",
                        "data": {
                            "leader": "coinbase" if leader == 'cb' else leader,
                            "followers": [f for f in followers],
                            "direction": direction,
                            "magnitude_bps": round(abs(vel) * 10000, 2),
                            "expected_lag_ms": 180 # Hardcoded for now per example
                        }
                    })

    return events
