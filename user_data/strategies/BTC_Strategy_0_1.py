"""
**BTC_Strategy_0.1** — Freqtrade class ``BTC_Strategy_0_1`` (underscore; Python id).

Spec: ``letscrash/BTC_Strategy_0.1.md`` + ``btc_strategy_0_1_rule_registry.json``.
Adds **tag-specific** entry/exit paths for **BTC-0.1-R01–R03**: each path is selected by
``enter_tag`` (``BTC-0.1-R01`` … ``R03``) in ``populate_entry_trend`` / ``custom_exit`` /
``confirm_trade_entry`` (see ``letscrash/BTC_Strategy_0.1.md`` §7).
**R03** = **scalping pattern** (pullback sleeve + scalp TP/RSI exits in ``custom_exit``).

**Futures (USDT linear):** **leverage** is **not** set manually — inherited ``SygnifStrategy.leverage()`` (major tier, short cap, ATR vol scaling). **Entries / exits** run on **strategy math** (this class + parent); RPC ``forceenter`` is optional operator override only. With ``position_adjustment_enable`` in config, **scale-in** (multiple fills on the **same** trade) is allowed for R01–R03 and ``manual_*`` tags — not venue **hedge mode** long+short on one symbol under Freqtrade’s Bybit one-way default.

**Research data path:** **Nautilus** container (compose profile ``btc-nautilus``) processes Bybit/BTC feeds into ``finance_agent/btc_specialist/data/`` for training + **ruleprediction** — see ``letscrash/RULE_AND_DATA_FLOW_LOOP.md`` §3.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

import pandas as pd
from freqtrade.persistence import Trade

from SygnifStrategy import SygnifStrategy as _SygnifStrategyBase

import btc_strategy_0_1_engine as b01
import btc_trend_regime

logger = logging.getLogger(__name__)

try:
    from trade_overseer.event_log import EventLog
    from trade_overseer.risk_manager import RiskEngineConfig, RiskManager

    _HAS_OVERSEER = True
except ImportError:
    _HAS_OVERSEER = False


class BTC_Strategy_0_1(_SygnifStrategyBase):
    """Sygnif stack + BTC 0.1 rule tags, bucket cap, R01/R02/R03 exits; leverage from parent."""

    max_slots_btc_0_1_r03 = 3
    # --- Position adjustment (scale-in on same pair; NOT exchange hedge long+short) ---
    # Freqtrade sets Bybit **one-way** mode at startup; ``adjust_trade_position`` adds stake
    # to the **existing** trade (multiple fills / DCA), not a separate opposite leg.
    DCA_ELIGIBLE_TAGS = frozenset(_SygnifStrategyBase.DCA_ELIGIBLE_TAGS) | frozenset(
        {b01.TAG_R01, b01.TAG_R02, b01.TAG_R03}
    )
    DCA_MAX_ENTRIES = 3
    # Futures: allow opens when whitelist is BTC-only (``_active_volume_pairs`` < 3).
    # ``btc_analysis_consensus`` = RPC from ``scripts/btc_analysis_forceenter.py`` (prediction JSON + R01 gate).
    _tags_bypass_volume_regime = frozenset(
        {"BTC-0.1-R01", "BTC-0.1-R02", "BTC-0.1-R03", "btc_analysis_consensus"}
    )

    def bot_start(self, **kwargs) -> None:
        """BTC-only portfolio: skip movers/new_pairs injection (``StaticPairList`` is source of truth)."""
        if self.config.get("trading_mode", "") == "futures":
            self.can_short = True
        self._load_doom_cooldown()
        self._refresh_strategy_adaptation(force=True)
        self._risk_manager = None
        self._event_log = None
        self._last_sl_tier: dict[int, str] = {}
        if _HAS_OVERSEER:
            self._risk_manager = RiskManager(
                RiskEngineConfig(
                    ratchet_tiers=(
                        (0.10, 0.015),
                        (0.05, 0.02),
                    ),
                )
            )
            instance = (
                "freqtrade-futures" if self.config.get("trading_mode", "") == "futures" else "freqtrade"
            )
            self._event_log = EventLog(instance=instance)

    def bot_loop_start(self, current_time=None, **kwargs) -> None:
        """Keep ``StaticPairList``; refresh adaptation + futures volume regime count only."""
        self._refresh_strategy_adaptation(force=False)
        if not self.dp:
            return
        current_wl = self.dp.current_whitelist()
        if self.config.get("trading_mode", "") == "futures":
            count = 0
            for p in current_wl:
                try:
                    df, _ = self.dp.get_analyzed_dataframe(p, self.timeframe)
                    if len(df) > 0 and self._futures_volume_gate_passes(df):
                        count += 1
                except Exception:
                    pass
            self._active_volume_pairs = count

    def populate_entry_trend(self, df: pd.DataFrame, metadata: dict) -> pd.DataFrame:
        pair = metadata.get("pair", "")

        df = super().populate_entry_trend(df, metadata)

        # ``btc_trend`` profile: map parent ``btc_trend_long`` → **R02** tag, but do **not**
        # return early — R01 governance / ``strong_ta``→R01 / R03 sleeve must still run so
        # Docker btc-0-1 keeps full **R01–R03** entry + ``custom_exit`` paths alive.
        if (
            btc_trend_regime.sygnif_profile() == "btc_trend"
            and btc_trend_regime.is_btc_pair(pair)
            and len(df) > 0
        ):
            last_i = len(df) - 1
            if str(df.iloc[last_i].get("enter_tag") or "") == "btc_trend_long":
                df.iloc[last_i, df.columns.get_loc("enter_tag")] = b01.TAG_R02

        if not btc_trend_regime.is_btc_pair(pair) or len(df) < 6:
            return df

        last_i = len(df) - 1
        # --- R01 governance: strip aggressive long *timing* when training+runner stack is bearish ---
        if int(df.iloc[last_i].get("enter_long", 0) or 0) == 1 and b01.r01_training_runner_bearish():
            tag = str(df.iloc[last_i].get("enter_tag") or "")
            if tag == "strong_ta" or tag == "orb_long" or (
                tag.startswith("sygnif_s") and not tag.startswith("sygnif_short")
            ):
                df.iloc[last_i, df.columns.get_loc("enter_long")] = 0
                df.iloc[last_i, df.columns.get_loc("enter_tag")] = ""

        # --- R01 entry tag: BTC ``strong_ta`` that passed governance → explicit tag for exits/journal ---
        if int(df.iloc[last_i].get("enter_long", 0) or 0) == 1:
            t = str(df.iloc[last_i].get("enter_tag") or "")
            if t == "strong_ta":
                df.iloc[last_i, df.columns.get_loc("enter_tag")] = b01.TAG_R01

        # --- R03 scalping-pattern sleeve (last bar; blocked under R01 extreme stack) ---
        if int(df.iloc[last_i].get("enter_long", 0) or 0) == 0 and not b01.r01_training_runner_bearish():
            prot = df.get("protections_long_global", pd.Series(True, index=df.index))
            empty_ok = df.get("num_empty_288", pd.Series(0, index=df.index)).fillna(0) <= 60
            if bool(prot.iloc[last_i]) and bool(empty_ok.iloc[last_i]) and b01.r03_pullback_long(df):
                df.iloc[last_i, df.columns.get_loc("enter_long")] = 1
                df.iloc[last_i, df.columns.get_loc("enter_tag")] = b01.TAG_R03

        return df

    def confirm_trade_entry(
        self,
        pair,
        order_type,
        amount,
        rate,
        time_in_force,
        current_time,
        entry_tag,
        side,
        **kwargs,
    ):
        tag = entry_tag or ""
        open_trades = Trade.get_trades_proxy(is_open=True)

        if btc_trend_regime.is_btc_pair(pair) and side == "long" and b01.r01_training_runner_bearish():
            if tag in (b01.TAG_R01, "strong_ta", "orb_long") or (
                tag.startswith("sygnif_s") and not tag.startswith("sygnif_short")
            ):
                logger.info("BTC-0.1-R01 governance: blocking %s %s under bearish training+runner", tag, pair)
                return False

        if tag.startswith("BTC-0.1-R"):
            cap = b01.load_notional_cap_usdt()
            used = b01.bucket_used_stake_usdt(open_trades)
            est = float(amount or 0.0) * float(rate or 0.0)
            if used + est > cap * 1.02:
                logger.info(
                    "BTC-0.1 bucket cap: used=%.0f est=%.0f cap=%.0f — block %s",
                    used,
                    est,
                    cap,
                    pair,
                )
                return False

        if tag == b01.TAG_R02:
            n = sum(1 for t in open_trades if (t.enter_tag or "") == b01.TAG_R02)
            if n >= int(getattr(self, "max_slots_btc_trend", 2)):
                logger.info("BTC-0.1-R02 slot cap %s/%s", n, getattr(self, "max_slots_btc_trend", 2))
                return False

        if tag == b01.TAG_R03:
            n = sum(1 for t in open_trades if (t.enter_tag or "") == b01.TAG_R03)
            if n >= int(getattr(self, "max_slots_btc_0_1_r03", 3)):
                logger.info("BTC-0.1-R03 slot cap %s/%s", n, getattr(self, "max_slots_btc_0_1_r03", 3))
                return False

        if tag == b01.TAG_R01:
            n = sum(1 for t in open_trades if (t.enter_tag or "") == b01.TAG_R01)
            cap_slots = int(getattr(self, "max_slots_strong", 6))
            if n >= cap_slots:
                logger.info("BTC-0.1-R01 slot cap %s/%s (same band as strong_ta)", n, cap_slots)
                return False

        # RPC / operator forceenter (e.g. manual_demo_open): bypass Sygnif volume/premium gates
        # so BTC-only whitelist still allows a demo order when <3 alts pass volume regime.
        if str(tag).startswith("manual_"):
            return True

        return super().confirm_trade_entry(
            pair,
            order_type,
            amount,
            rate,
            time_in_force,
            current_time,
            entry_tag,
            side,
            **kwargs,
        )

    def adjust_trade_position(
        self,
        trade: Trade,
        current_time: datetime,
        current_rate: float,
        current_profit: float,
        min_stake: Optional[float],
        max_stake: float,
        current_entry_rate: float,
        current_exit_rate: float,
        current_entry_profit: float,
        current_exit_profit: float,
        **kwargs,
    ) -> Optional[float]:
        """Same-pair scale-in (parent DCA math) for R01–R03 + ``manual_*`` RPC tags."""
        tag = trade.enter_tag or ""
        if not (
            tag in self.DCA_ELIGIBLE_TAGS
            or tag.startswith("manual_")
            or tag.startswith("BTC-0.1-R")
        ):
            return None

        filled = trade.nr_of_successful_entries
        if filled > int(getattr(self, "DCA_MAX_ENTRIES", 3)):
            return None
        if current_profit > float(getattr(self, "DCA_DRAWDOWN_STEP", -0.03)):
            return None

        original_stake = trade.stake_amount / max(filled, 1)
        dca_stake = original_stake * float(getattr(self, "DCA_SCALE_FACTOR", 0.5))
        dca_stake = max(min_stake or 0, min(dca_stake, max_stake))

        logger.info(
            "BTC-0.1 scale-in: %s tag=%s entries=%d profit=%.2f%% → +%.2f USDT",
            trade.pair,
            tag,
            filled,
            current_profit * 100,
            dca_stake,
        )
        return dca_stake

    def custom_stoploss(
        self,
        pair: str,
        trade: Trade,
        current_time: datetime,
        current_rate: float,
        current_profit: float,
        after_fill: bool,
        **kwargs,
    ) -> float:
        """R03 sleeve: cap doom vs Sygnif parent to the ruleprediction first-trade SL box (§7.1)."""
        tag = trade.enter_tag or ""
        parent_sl = super().custom_stoploss(
            pair, trade, current_time, current_rate, current_profit, after_fill, **kwargs
        )
        if tag == b01.TAG_R03 and not trade.is_short:
            return max(parent_sl, b01.R03_STOPLOSS_FLOOR_VS_PARENT)
        return parent_sl

    def custom_exit(
        self,
        pair: str,
        trade: Trade,
        current_time: datetime,
        current_rate: float,
        current_profit: float,
        **kwargs,
    ):
        tag = trade.enter_tag or ""
        lev = float(trade.leverage or 1.0)

        if not trade.is_short and btc_trend_regime.is_btc_pair(pair):
            df, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
            if len(df) >= 1:
                last = df.iloc[-1]
                if tag == b01.TAG_R01 and b01.r01_training_runner_bearish():
                    return "exit_btc01_r01_stack_guard"
                if tag == b01.TAG_R02 and not btc_trend_regime.btc_trend_long_row(last):
                    return "exit_btc01_r02_regime_break"
                if tag == b01.TAG_R03:
                    rsi14 = float(last.get("RSI_14", 50) or 50)
                    if rsi14 > b01.R03_SCALP_RSI_OVERBOUGHT:
                        return "exit_btc01_r03_scalp_overbought"
                    if current_profit >= b01.R03_SCALP_TP_PROFIT_PCT * max(1.0, lev):
                        return "exit_btc01_r03_scalp_take"
                if tag in (b01.TAG_R02, b01.TAG_R03) and b01.r01_training_runner_bearish():
                    if current_profit < b01.R01_R03_STACK_GUARD_LOSS_PCT * max(1.0, lev):
                        return "exit_btc01_r01_stack_guard"

        return super().custom_exit(
            pair, trade, current_time, current_rate, current_profit, **kwargs
        )
