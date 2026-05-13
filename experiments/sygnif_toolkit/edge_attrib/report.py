import json
import argparse
import pandas as pd

def generate_report(rts, window="7d"):
    df = pd.DataFrame(rts)
    if df.empty:
        print("No round trips to report.")
        return

    df['entry_time'] = pd.to_datetime(df['entry_ts'], unit='ms')
    df['hour_of_day'] = df['entry_time'].dt.hour

    # $/trade by strategy, by hour-of-day, by side, by order_type
    groupby_cols = ['strategy', 'hour_of_day', 'side', 'order_type_entry']
    grouped = df.groupby(groupby_cols).agg(
        trades=('entry_ts', 'count'),
        mean_realized=('realized_pnl', 'mean'),
        sum_signal=('signal_pnl', 'sum'),
        sum_entry_slip=('entry_slippage', 'sum'),
        sum_exit_slip=('exit_slippage', 'sum'),
        sum_fees=('fee_pnl', 'sum')
    ).reset_index()

    print("=== Edge Attribution Report ===")
    print(grouped.to_string())

    # Edge bleed breakdown: % of gross signal_pnl lost to each component
    total_signal = df['signal_pnl'].sum()
    if total_signal != 0:
        entry_slip_pct = (df['entry_slippage'].sum() / total_signal) * 100
        exit_slip_pct = (df['exit_slippage'].sum() / total_signal) * 100
        fee_pct = (df['fee_pnl'].sum() / total_signal) * 100

        print("\n=== Edge Bleed Breakdown ===")
        print(f"Total Signal PnL: ${total_signal:.2f}")
        print(f"Lost to Entry Slippage: {entry_slip_pct:.2f}%")
        print(f"Lost to Exit Slippage: {exit_slip_pct:.2f}%")
        print(f"Lost to Fees: {fee_pct:.2f}%")

        leaks = {
            "Entry Slippage": df['entry_slippage'].sum(),
            "Exit Slippage": df['exit_slippage'].sum(),
            "Fees": df['fee_pnl'].sum()
        }
        # Find biggest negative leak
        biggest_leak_name = min(leaks, key=leaks.get)
        print(f"\nSingle biggest leak: {biggest_leak_name} (${leaks[biggest_leak_name]:.2f})")

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('decomposed_file', help="Path to decomposed jsonl")
    parser.add_argument('--window', default="7d")
    args = parser.parse_args()

    rts = []
    with open(args.decomposed_file, 'r') as f:
        for line in f:
            if line.strip():
                rts.append(json.loads(line))

    generate_report(rts, args.window)

if __name__ == '__main__':
    main()
