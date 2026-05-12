# Price Check Agent

Quick price lookup for a specific cryptocurrency.

## Variables
- **TICKER**: The coin to check (default: BTC)

## Task

1. Fetch current price from Bybit: `GET https://api.bybit.com/v5/market/tickers?category=spot&symbol={TICKER}USDT`
2. Return price, 24h change, and volume

## Output Format

```
Generated: {UTC timestamp}

**{TICKER}/USDT:** ${price} ({+/-X.XX}%) | Vol: ${volume}
```

## Rules
- Single line output unless more detail requested
- Real data from Bybit API only
