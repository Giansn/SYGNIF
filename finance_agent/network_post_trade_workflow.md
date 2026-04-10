# Post-trade network workflow (finance-agent / Cursor training)

Template for **deterministic** post-trade analysis using **repo + SQLite + Docker logs + Bybit + honest epistemics**. Goal: a **repeatable five-phase flow** — not a one-off oracle.

---

## Flow (required order)

### Phase 1 — **Fetch outcome**

**Goal:** a fact base with no interpretation.

| Item | How |
|------|-----|
| Trade core | `trades`: `pair`, `is_short`, `enter_tag`, `open_date`, `close_date`, `exit_reason`, `close_profit` / `close_profit_abs`, `open_rate`, `close_rate`, `leverage`, `trading_mode` |
| Spot + futures | query both DBs: `user_data/tradesv3.sqlite`, `user_data/tradesv3-futures.sqlite` |
| Order / stop path | `orders` for `ft_trade_id`: `ft_order_side`, `stop_price`, `status`, `order_date`, `ft_order_tag` |
| Log cross-check | `docker logs freqtrade` / `freqtrade-futures` — `Exit … Reason`, stop cancel/replace, active strategy class |

```bash
# Find trades
sqlite3 user_data/tradesv3.sqlite \
  "SELECT id, pair, is_short, enter_tag, open_date, close_date, exit_reason, close_profit, leverage FROM trades WHERE pair LIKE '%PAIR%' ORDER BY close_date DESC LIMIT 10;"
sqlite3 user_data/tradesv3-futures.sqlite \
  "SELECT id, pair, is_short, enter_tag, open_date, close_date, exit_reason, close_profit, leverage FROM trades WHERE pair LIKE '%PAIR%' ORDER BY close_date DESC LIMIT 10;"

# Orders
sqlite3 user_data/tradesv3-futures.sqlite \
  "SELECT id, ft_order_side, ft_price, stop_price, status, order_date, ft_order_tag FROM orders WHERE ft_trade_id = TRADE_ID ORDER BY id;"
```

**Phase output:** a compact **facts table** (one trade, numbers, times in UTC).

---

### Phase 2 — **Compare to thesis**

**Goal:** derive the entry thesis from **code**, not intuition.

1. Read **`enter_tag`** (e.g. `strong_ta`, `sygnif_swing`, `sygnif_s5`).
2. Look up **`SygnifStrategy.py` / `MarketStrategy2.py`**: **what conditions** produce this tag? (TA bands, volume, sentiment path yes/no.)
3. **State the thesis explicitly** — one sentence, e.g.:  
   *“`strong_ta` long: high TA score + volume gate; short-horizon bullish setup, no sentiment requirement.”*

**Phase output:** **thesis (1–3 sentences)** + **pointer** to strategy logic / tag semantics.

---

### Phase 3 — **Win / fail analysis**

**Goal:** judge **P&amp;L + mechanics + thesis**.

| Question | Answer |
|----------|--------|
| Win / fail? | `close_profit` / `close_profit_abs` → **profit / loss / breakeven** |
| Did price move **against** the thesis? | Direction: long + price **below** entry matters; mirror for shorts |
| **Why** closed? | `exit_reason` + orders (`trailing_stop_loss`, `stoploss_on_exchange`, `exit_sf_*`, …) vs **`custom_stoploss` / `custom_exit`** |
| Thesis vs outcome | e.g. *thesis bullish short-term; outcome: drawdown + trail exit* → **fail on the position’s horizon** without automatically claiming “strategy is wrong” |

**Phase output:** **win/fail** + **thesis violated yes/no (name the horizon)** + **mechanical exit explanation** (no hand-wavy “the market decided”).

---

### Phase 4 — **Price after exit + post-hoc thesis**

**Goal:** market data **after** `close_date` / fill time — **descriptive only**.

1. Bybit v5 `GET /v5/market/kline` (`category=linear` or `spot`), 5m/15m bars, **from exit time onward**.
2. Low / high / close **vs exit reference price** (e.g. `close_rate` or fill).
3. Frame any “new thesis” only as **what you could say after the fact** — label clearly: **post-hoc**, no retroactive justification of the original entry.

**Phase output:** small **price table** (candles after exit) + one **cautious narrative** (“after exit: dip then recovery above exit”) stamped **retrospective**.

---

### Phase 5 — **Could you have predicted post-exit movement?**

**Goal:** **epistemics** — mandatory close.

| Claim | Allowed? |
|-------|----------|
| Exact path (minute, every bar) **ex ante** | **No** reliably |
| Qualitative: “volatile, whipsaw possible” | **Yes** if pair / ATR / context support it |
| “Could you see the **stop** coming?” | **Partly:** if mechanics (`stoploss_on_exchange` + `custom_stoploss`) are known |

**Phase output:** 3–5 bullets — **what was knowable beforehand** vs **what is hindsight only**.

---

## Data sources (“network”)

| Layer | Source |
|-------|--------|
| Persistence | `trades`, `orders`, optional `trade_custom_data` |
| Logs | `docker logs freqtrade` / `freqtrade-futures` |
| Market | Bybit `kline` |
| Optional | Overseer `http://127.0.0.1:8090/overview` |
| Code | `SygnifStrategy.py`, `MarketStrategy2.py` |

**Cursor worker:** reach services via **`127.0.0.1` + ports**; Docker DNS (`finance-agent`) only **inside** containers.

---

## Report checklist

1. **Phase 1** — facts table  
2. **Phase 2** — thesis + code reference  
3. **Phase 3** — win/fail + thesis vs outcome + exit mechanics  
4. **Phase 4** — post-exit price + **retrospective** thesis  
5. **Phase 5** — **honest** bounds on predictability  

---

## Optional extensions

- `scripts/prediction_horizon_check.py` — mechanically check stored scenarios.  
- `trade_overseer/entry_performance.py` — tag families over time.  
- GitNexus — for “where does code set X?”

---

## Training / API

Canonical in-repo; **`GET /training`** exposes the absolute path in **`post_trade_network_workflow`**.
