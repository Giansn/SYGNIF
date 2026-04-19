"""
**BTC_Strategy_0.1** — Freqtrade class ``BTC_Strategy_0_1`` (underscore; Python id).

Spec: ``letscrash/BTC_Strategy_0.1.md`` + ``btc_strategy_0_1_rule_registry.json``.
Adds **tag-specific** entry/exit paths for **BTC-0.1-R01–R03**: each path is selected by
``enter_tag`` (``BTC-0.1-R01`` … ``R03``) in ``populate_entry_trend`` / ``custom_exit`` /
``confirm_trade_entry`` (see ``letscrash/BTC_Strategy_0.1.md`` §7).
**R03** = **scalping pattern** (pullback sleeve + scalp TP/RSI exits in ``custom_exit``).

**Futures (USDT linear):** **leverage** is **not** set manually — inherited ``SygnifStrategy.leverage()`` (major tier, short cap, ATR vol scaling). **Entries / exits** run on **strategy math** (this class + parent); RPC ``forceenter`` is optional operator override only. With ``position_adjustment_enable`` in config, **scale-in** (multiple fills on the **same** trade) is allowed for R01–R03 and ``manual_*`` tags — not venue **hedge mode** long+short on one symbol under Freqtrade’s Bybit one-way default.

**Research data path:** refresh ``finance_agent/btc_specialist/data/`` via host/cron (**``pull_btc_context.py``**, **``research/nautilus_lab/``** scripts, or other jobs) for training + **ruleprediction** — see ``letscrash/RULE_AND_DATA_FLOW_LOOP.md`` §3.

**TP / SL (registry):** ``tuning.tp_sl`` + ``tuning.entry_prediction`` in ``btc_strategy_0_1_rule_registry.json`` — tighter SL via ``custom_stoploss``, TP via ``custom_exit``; futures keep ``stoploss_on_exchange`` from config.

**Trailing TP/SL (optional, inherited):** ``SYGNIF_TRAILING_TPSL=1`` — combined trail + entry SL + post-TP lock in parent ``custom_stoploss``; trailing take-profit (callback from peak) in ``custom_exit`` (see ``.cursor/rules/trailing-tpsl.mdc``). This class still applies registry ``tag_sl_return_cap`` and the R03 stop floor **after** ``super().custom_stoploss``. In ``custom_exit``, trailing TP is evaluated **after** R01 stack guard + R02 regime break and **before** fixed registry TP / R03 scalp targets.

**Swarm + win:** When Swarm reads **against** the long (``swarm_adverse_to_long`` on the last bar) and the trade is **already in profit**, a **tighter** trailing take-profit (``exit_swarm_trailing_win``) runs **before** the generic trailing TP — smaller pullback from ``trade.max_rate`` (``SYGNIF_TS_SWARM_CALLBACK_PCT`` / ``tuning.swarm_trail_tp``).

**Swarm:** ``btc_strategy_0_1_engine`` loads ``swarm_knowledge_output.json`` (path via ``SYGNIF_PREDICTION_AGENT_DIR`` in Docker). Columns ``swarm_mean`` / ``swarm_label`` / ``swarm_conflict``; optional long veto when ``SYGNIF_BTC01_SWARM_ROOT=1`` (bearish / conflict rules — no new orders from Swarm, signal-only).
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

    # --- Position adjustment (scale-in on same pair; NOT exchange hedge long+short) ---
    # Freqtrade sets Bybit **one-way** mode at startup; ``adjust_trade_position`` adds stake
    # to the **existing** trade (multiple fills / DCA), not a separate opposite leg.
    DCA_ELIGIBLE_TAGS = frozenset(_SygnifStrategyBase.DCA_ELIGIBLE_TAGS) | frozenset(
        {b01.TAG_R01, b01.TAG_R02, b01.TAG_R03}
    )
    DCA_MAX_ENTRIES = 3
    DCA_DRAWDOWN_STEP = -0.03
    # Futures: allow opens when whitelist is BTC-only (``_active_volume_pairs`` < 3).
    # ``btc_analysis_consensus`` = RPC from ``scripts/btc_analysis_forceenter.py`` (prediction JSON + R01 gate).
    _tags_bypass_volume_regime = frozenset(
        {"BTC-0.1-R01", "BTC-0.1-R02", "BTC-0.1-R03", "btc_analysis_consensus"}
    )

    def bot_start(self, **kwargs) -> None:
        """BTC-only portfolio: skip movers/new_pairs injection (``StaticPairList`` is source of truth)."""
        if self.config.get("trading_mode", "") == "futures":
            self.can_short = True
        # Parent sets these in ``SygnifStrategy.bot_start``; we do not call super (no movers refresh).
        self._nautilus_signal_mtime = -1.0
        self._nautilus_signal_doc = None
        # Never use shared movers / new_pairs files (BTC-only bot).
        self._movers_pairs = []
        self._new_pairs = []
        # Isolated doom file: spot + futures both mount ``user_data/``; sharing
        # ``doom_cooldown.json`` lets one bot overwrite the other's cooldown map and
        # leaves BTC/USDT:USDT stuck in 4h doom in-memory while JSON has no :USDT key.
        self._doom_cooldown_path = "user_data/doom_cooldown_futures_btc01.json"
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

        dca_t = (b01.tuning_config().get("dca") or {})
        me = dca_t.get("max_entries")
        if me is not None:
            self.DCA_MAX_ENTRIES = max(1, int(me))
        dd = dca_t.get("drawdown_step")
        if dd is not None:
            self.DCA_DRAWDOWN_STEP = float(dd)

    def _refresh_movers(self) -> None:
        """No-op: do not load ``movers_pairlist.json`` (parent would pollute informative pairs)."""
        self._movers_pairs = []

    def _refresh_new_pairs(self) -> None:
        """No-op: do not load ``new_pairs.json``."""
        self._new_pairs = []

    def informative_pairs(self):
        """BTC + current whitelist only (no movers / new_pairs)."""
        is_futures = self.config.get("trading_mode", "") == "futures"
        btc_pair = "BTC/USDT:USDT" if is_futures else "BTC/USDT"
        pairs = []
        for tf in self.info_timeframes:
            pairs.append((btc_pair, tf))
        if self.dp:
            for pair in self.dp.current_whitelist():
                for tf in self.info_timeframes:
                    pairs.append((pair, tf))
        return list(dict.fromkeys(pairs))

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
        b01.attach_swarm_columns(df)

        # Swarm root gate: optional veto of long entries from ``swarm_knowledge_output.json``
        if btc_trend_regime.is_btc_pair(pair) and len(df) > 0 and b01.swarm_root_blocks_long():
            last_i = len(df) - 1
            if int(df.iloc[last_i].get("enter_long", 0) or 0) == 1:
                df.iloc[last_i, df.columns.get_loc("enter_long")] = 0
                df.iloc[last_i, df.columns.get_loc("enter_tag")] = ""

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
            # R02 finetune: registry ``tuning.r02_regime`` (defaults = Sygnif btc_trend); strip if stricter gate fails
            if str(df.iloc[last_i].get("enter_tag") or "") == b01.TAG_R02:
                if not b01.btc01_r02_trend_long_row(df.iloc[last_i]):
                    df.iloc[last_i, df.columns.get_loc("enter_long")] = 0
                    df.iloc[last_i, df.columns.get_loc("enter_tag")] = ""

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
            if b01.entry_prediction_blocks_long_under_bearish(tag):
                logger.info(
                    "BTC-0.1 entry_prediction: blocking long %s %s under bearish training+runner",
                    tag,
                    pair,
                )
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
            cap_r2 = b01.slot_cap_r02()
            n = sum(1 for t in open_trades if (t.enter_tag or "") == b01.TAG_R02)
            if n >= cap_r2:
                logger.info("BTC-0.1-R02 slot cap %s/%s", n, cap_r2)
                return False

        if tag == b01.TAG_R03:
            cap_r3 = b01.slot_cap_r03()
            n = sum(1 for t in open_trades if (t.enter_tag or "") == b01.TAG_R03)
            if n >= cap_r3:
                logger.info("BTC-0.1-R03 slot cap %s/%s", n, cap_r3)
                return False

        if tag == b01.TAG_R01:
            cap_r1 = b01.slot_cap_r01()
            n = sum(1 for t in open_trades if (t.enter_tag or "") == b01.TAG_R01)
            if n >= cap_r1:
                logger.info("BTC-0.1-R01 slot cap %s/%s (same band as strong_ta)", n, cap_r1)
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
        """Registry SL cap + R03 floor on top of parent (parent includes optional trailing TPSL when env set)."""
        tag = trade.enter_tag or ""
        parent_sl = super().custom_stoploss(
            pair, trade, current_time, current_rate, current_profit, after_fill, **kwargs
        )
        is_futures = self.config.get("trading_mode", "") == "futures"
        cap = b01.tag_sl_return_cap(trade, tag, is_futures=is_futures)
        if cap is not None:
            parent_sl = max(parent_sl, cap)
        if tag == b01.TAG_R03 and not trade.is_short:
            return max(parent_sl, b01.R03_STOPLOSS_FLOOR_VS_PARENT)
        return parent_sl

    def _btc01_swarm_trailing_take_win(
        self,
        trade: Trade,
        current_rate: float,
        current_profit: float,
        leverage: float,
        last,
    ) -> Optional[str]:
        """Tight trailing TP when Swarm disagrees with the long — bank profit on a small dip."""
        if not self._sygnif_trailing_tpsl_enabled():
            return None
        if getattr(trade, "is_short", False) or current_profit <= 0:
            return None
        if not b01.swarm_adverse_to_long(last):
            return None
        lev_m = max(1.0, float(leverage))
        if current_profit < b01.swarm_trail_min_profit_gate(lev_m):
            return None
        entry = float(trade.open_rate)
        if entry <= 0 or current_rate <= 0:
            return None
        xr = getattr(trade, "max_rate", None)
        peak = float(xr) if xr is not None and float(xr) > 0 else current_rate
        peak = max(peak, current_rate, entry)
        cb = b01.swarm_trail_callback_pct()
        if current_rate <= peak * (1.0 - cb):
            return "exit_swarm_trailing_win"
        return None

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
                if tag == b01.TAG_R02 and not b01.btc01_r02_trend_long_row(last):
                    return "exit_btc01_r02_regime_break"
                swx = self._btc01_swarm_trailing_take_win(trade, current_rate, current_profit, lev, last)
                if swx:
                    return swx
                # Same trailing take-profit path as SygnifStrategy (SYGNIF_TRAILING_TPSL=1), before fixed tag TPs.
                tpx = self._sygnif_exit_trailing_take_profit(trade, current_rate)
                if tpx:
                    return tpx
                tp_pct = b01.tag_takeprofit_profit_pct(tag)
                if tp_pct is not None and tag in (b01.TAG_R01, b01.TAG_R02):
                    if current_profit >= tp_pct * max(1.0, lev):
                        return "exit_btc01_r01_tp" if tag == b01.TAG_R01 else "exit_btc01_r02_tp"
                if tag == b01.TAG_R03:
                    rsi14 = float(last.get("RSI_14", 50) or 50)
                    if rsi14 > b01.r03_scalp_rsi_overbought():
                        return "exit_btc01_r03_scalp_overbought"
                    r3_tp = b01.tag_takeprofit_profit_pct(b01.TAG_R03)
                    if r3_tp is not None and current_profit >= r3_tp * max(1.0, lev):
                        return "exit_btc01_r03_scalp_take"
                if tag in (b01.TAG_R02, b01.TAG_R03) and b01.r01_training_runner_bearish():
                    if current_profit < b01.r01_r03_stack_guard_loss_pct() * max(1.0, lev):
                        return "exit_btc01_r01_stack_guard"

        return super().custom_exit(
            pair, trade, current_time, current_rate, current_profit, **kwargs
        )
