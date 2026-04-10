"""
Sygnif Reproducible Research — modeled on NautilusTrader's BacktestEngine run config.

NT reference implementation:
  - crates/backtest/src/engine/mod.rs     → BacktestEngine (run, reset, dispose)
  - crates/backtest/src/config.rs         → BacktestRunConfig, BacktestDataConfig,
                                            BacktestVenueConfig, BacktestEngineConfig
  - nautilus_trader/backtest/engine.py    → BacktestEngine (Python API)
  - nautilus_trader/backtest/config.py    → BacktestRunConfig (Pydantic)

NT BacktestRunConfig captures everything needed to reproduce a run:
  1. BacktestEngineConfig  → risk_engine, catalog, environment
  2. BacktestDataConfig    → data_cls, instrument_id, start_time, end_time
  3. BacktestVenueConfig   → venue, account_type, starting_balances,
                             default_leverage, fill_model, fee_model, latency_model
  4. batch_size_bytes      → chunk size for streaming data

SYGNIF mapping: Capture equivalent metadata for Freqtrade research runs so any
scan, backtest, or analysis can be exactly reproduced.

Usage:
  from research_metadata import ResearchRun

  run = ResearchRun.start(
      pipeline="market_scan",
      pairs=["BTC/USDT", "ETH/USDT"],
      timeframe="5m",
      config={"ta_strong_threshold": 65, "vol_multiplier": 1.2},
      venue_config=VenueConfig(maker_fee=0.0002, taker_fee=0.00055),
  )
  # ... do analysis ...
  run.complete(output_path="outputs/scan_20260410.json")
  print(run.metadata)
"""
from __future__ import annotations

import hashlib
import json
import os
import platform
import sys
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional


def _config_hash(config: dict) -> str:
    serialized = json.dumps(config, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode()).hexdigest()[:16]


def _env_info() -> dict:
    info = {
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "host": platform.node(),
    }
    try:
        import pandas
        info["pandas"] = pandas.__version__
    except ImportError:
        pass
    try:
        import numpy
        info["numpy"] = numpy.__version__
    except ImportError:
        pass
    try:
        import freqtrade
        info["freqtrade"] = freqtrade.__version__
    except (ImportError, AttributeError):
        pass
    return info


@dataclass
class VenueConfig:
    """Mirrors NT BacktestVenueConfig (crates/backtest/src/config.rs).

    NT fields: venue, oms_type, account_type, starting_balances,
    default_leverage, fill_model, fee_model, latency_model, routing
    """
    venue: str = "BYBIT"
    account_type: str = "MARGIN"
    starting_balance: float = 240.0
    base_currency: str = "USDT"
    default_leverage: float = 3.0
    maker_fee: float = 0.0002
    taker_fee: float = 0.00055
    fill_model: str = "latency"
    latency_ms: float = 50.0
    funding_rate: float = 0.0001

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class DataConfig:
    """Mirrors NT BacktestDataConfig (crates/backtest/src/config.rs).

    NT fields: data_cls, instrument_id, start_time, end_time, catalog_path
    """
    exchange: str = "bybit"
    pairs: list[str] = field(default_factory=list)
    timeframe: str = "5m"
    start_ts: Optional[str] = None
    end_ts: Optional[str] = None
    candle_count: int = 0
    catalog_path: Optional[str] = None

    @property
    def snapshot_id(self) -> str:
        raw = (
            f"{self.exchange}|{'|'.join(sorted(self.pairs))}|"
            f"{self.timeframe}|{self.start_ts}|{self.end_ts}"
        )
        return hashlib.sha256(raw.encode()).hexdigest()[:12]

    def to_dict(self) -> dict:
        d = asdict(self)
        d["snapshot_id"] = self.snapshot_id
        return d


@dataclass
class EngineConfig:
    """Mirrors NT BacktestEngineConfig subset.

    NT fields: trader_id, log_level, risk_engine (RiskEngineConfig),
    exec_engine (ExecEngineConfig), cache (CacheConfig), streaming
    """
    trader_id: str = "SYGNIF-001"
    strategy_id: str = "SygnifStrategy"
    risk_engine_bypass: bool = False
    exec_engine_debug: bool = False
    log_level: str = "INFO"

    def to_dict(self) -> dict:
        return asdict(self)


class ResearchRun:
    """Wraps a research pipeline run with NT BacktestRunConfig-equivalent metadata.

    NT BacktestRunConfig = EngineConfig + [DataConfig] + [VenueConfig] + batch_size
    SYGNIF ResearchRun = EngineConfig + DataConfig + VenueConfig + strategy params
    """

    def __init__(
        self,
        run_id: str,
        pipeline: str,
        data: DataConfig,
        venue: VenueConfig,
        engine: EngineConfig,
        strategy_config: dict,
        config_hash: str,
    ):
        self.run_id = run_id
        self.pipeline = pipeline
        self.data = data
        self.venue = venue
        self.engine = engine
        self.strategy_config = strategy_config
        self.config_hash = config_hash
        self.started_at = datetime.now(timezone.utc).isoformat()
        self.completed_at: Optional[str] = None
        self.output_path: Optional[str] = None
        self.status = "running"
        self.env = _env_info()

    @classmethod
    def start(
        cls,
        pipeline: str,
        pairs: Optional[list[str]] = None,
        timeframe: str = "5m",
        config: Optional[dict] = None,
        exchange: str = "bybit",
        start_ts: Optional[str] = None,
        venue_config: Optional[VenueConfig] = None,
        engine_config: Optional[EngineConfig] = None,
    ) -> ResearchRun:
        """Start a new tracked research run (NT BacktestEngine.run() equivalent)."""
        config = config or {}
        data = DataConfig(
            exchange=exchange,
            pairs=sorted(pairs or []),
            timeframe=timeframe,
            start_ts=start_ts,
        )
        return cls(
            run_id=uuid.uuid4().hex[:10],
            pipeline=pipeline,
            data=data,
            venue=venue_config or VenueConfig(),
            engine=engine_config or EngineConfig(),
            strategy_config=config,
            config_hash=_config_hash(config),
        )

    def complete(self, output_path: Optional[str] = None, candle_count: int = 0):
        """Mark run as complete (NT BacktestEngine stores results in kernel)."""
        self.completed_at = datetime.now(timezone.utc).isoformat()
        self.output_path = output_path
        self.status = "completed"
        self.data.candle_count = candle_count

    def fail(self, error: str):
        self.completed_at = datetime.now(timezone.utc).isoformat()
        self.status = f"failed: {error}"

    @property
    def metadata(self) -> dict:
        """Full NT BacktestRunConfig-equivalent metadata."""
        return {
            "run_id": self.run_id,
            "pipeline": self.pipeline,
            "status": self.status,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "data_config": self.data.to_dict(),
            "venue_config": self.venue.to_dict(),
            "engine_config": self.engine.to_dict(),
            "strategy_config": self.strategy_config,
            "config_hash": self.config_hash,
            "output_path": self.output_path,
            "environment": self.env,
        }

    def save_metadata(self, path: Optional[str] = None):
        if path is None:
            if self.output_path:
                path = self.output_path.rsplit(".", 1)[0] + ".meta.json"
            else:
                path = f"research_run_{self.run_id}.meta.json"
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w") as f:
            json.dump(self.metadata, f, indent=2)
        return path

    def embed_in_output(self, output: dict) -> dict:
        output["_research_metadata"] = self.metadata
        return output

    def reproduce_command(self) -> str:
        """Generate a shell command that reproduces this run (NT reset+run pattern)."""
        pairs_str = " ".join(self.data.pairs)
        cmd_parts = [
            f"python3 trade_overseer/perf_analysis.py",
            f"--db user_data/tradesv3-futures.sqlite",
        ]
        if self.data.start_ts:
            days = (datetime.now(timezone.utc) -
                    datetime.fromisoformat(self.data.start_ts)).days
            cmd_parts.append(f"--days {days}")
        return " ".join(cmd_parts)


# ── Log to JSONL for trend tracking ────────────────────────────────────────

_LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
_LOG_FILE = os.path.join(_LOG_DIR, "research_runs.jsonl")


def log_run(run: ResearchRun):
    os.makedirs(_LOG_DIR, exist_ok=True)
    with open(_LOG_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(run.metadata, separators=(",", ":"), default=str) + "\n")
