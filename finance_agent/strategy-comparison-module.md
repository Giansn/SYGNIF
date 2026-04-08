# Strategy Comparison Module (CUR-6)

## Tags

- `swing_failure`
- `claude_swing`
- Baseline: `claude_s0`

## Required metrics per tag

- Trade count
- Win rate
- Total P/L
- Average P/L per trade
- Average duration

## Baseline delta outputs

- Delta win rate vs baseline
- Delta avg P/L vs baseline
- Delta total P/L vs baseline
- Delta duration vs baseline
- Verdict: `BETTER` / `MIXED` / `WORSE`

## Policy

- Sample threshold: minimum 30 trades per tag before rank decisions.
- Two consecutive `WORSE` windows -> demote tag.
- Two consecutive `BETTER` windows with sample threshold met -> promote tag.
- If insufficient sample -> report only, no promote/demote.
