import pandas as pd
import numpy as np
from SygnifStrategy import compute_fibonacci_levels, detect_support_resistance, detect_swing_failure

def test_compute_fibonacci_levels():
    high = 100.0
    low = 0.0
    levels = compute_fibonacci_levels(high, low)

    assert levels['fib_0.0'] == 0.0
    assert levels['fib_1.0'] == 100.0
    assert levels['fib_0.5'] == 50.0
    assert levels['fib_0.618'] == 61.8
    assert levels['fib_0.382'] == 38.2

def test_detect_support_resistance():
    # Create a simple price series
    data = {
        'high': [10, 15, 20, 18, 15, 25, 22, 20],
        'low':  [5,  8,  12, 10, 8,  15, 12, 10]
    }
    df = pd.DataFrame(data)

    # window=3
    df_res = detect_support_resistance(df, window=3)

    # Verify strict trailing window (no center=True)
    # The rolling max of high with window=3 should be NaN for index 0 and 1
    assert np.isnan(df_res['rolling_high'].iloc[0])
    assert np.isnan(df_res['rolling_high'].iloc[1])
    assert df_res['rolling_high'].iloc[2] == 20
    assert df_res['rolling_high'].iloc[5] == 25

    # Check that there is no NaN at the end (which would happen with center=True)
    assert not np.isnan(df_res['rolling_high'].iloc[-1])
    assert not np.isnan(df_res['rolling_low'].iloc[-1])

def test_detect_swing_failure():
    data = {
        'high':  [10, 12, 14, 13, 15, 12, 16],
        'low':   [8,  10, 12, 11, 13, 10, 14],
        'close': [9,  11, 13, 12, 14, 11, 15]
    }
    df = pd.DataFrame(data)

    df_res = detect_swing_failure(df, lookback=3)

    assert 'fib_sfp_long' in df_res.columns
    assert 'fib_sfp_short' in df_res.columns

    # Ensure they are boolean
    assert df_res['fib_sfp_long'].dtype == bool
    assert df_res['fib_sfp_short'].dtype == bool
