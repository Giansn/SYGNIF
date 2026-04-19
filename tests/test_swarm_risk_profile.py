"""finance_agent/swarm_risk_profile.py + launcher merge sizing."""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]


def _load_swarm_auto_module():
    path = _REPO / "scripts" / "swarm_auto_predict_protocol_loop.py"
    spec = importlib.util.spec_from_file_location("_sapl_test", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["_sapl_test"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_normalize_risk_profile_aliases() -> None:
    from finance_agent.swarm_risk_profile import normalize_risk_profile

    assert normalize_risk_profile(None) == "default"
    assert normalize_risk_profile("") == "default"
    assert normalize_risk_profile("DEFAULT") == "default"
    assert normalize_risk_profile("legacy") == "default"
    assert normalize_risk_profile("demo_safe") == "demo_safe"
    assert normalize_risk_profile("demo-safe") == "demo_safe"
    assert normalize_risk_profile("safe") == "demo_safe"


def test_normalize_unknown_raises() -> None:
    from finance_agent.swarm_risk_profile import normalize_risk_profile

    with pytest.raises(ValueError):
        normalize_risk_profile("moon")


def test_demo_safe_overrides_nonempty() -> None:
    from finance_agent.swarm_risk_profile import risk_profile_env_overrides

    d = risk_profile_env_overrides("demo_safe")
    assert d["SYGNIF_PREDICT_DEFAULT_NOTIONAL_USDT"] == "5000"
    assert d["BYBIT_DEMO_ORDER_MAX_QTY"] == "0.05"
    assert d["SYGNIF_SWARM_TP_USDT_TARGET"] == "150"


def test_apply_to_isolated_environ() -> None:
    from finance_agent.swarm_risk_profile import apply_swarm_risk_profile

    env: dict[str, str] = {}
    applied = apply_swarm_risk_profile("demo_safe", environ=env)
    assert len(applied) >= 8
    assert env["SYGNIF_PREDICT_DEFAULT_MANUAL_LEVERAGE"] == "10"
    applied_default = apply_swarm_risk_profile("default", environ=env)
    assert applied_default == []


def test_resolve_cli_over_env(monkeypatch: pytest.MonkeyPatch) -> None:
    from finance_agent.swarm_risk_profile import resolve_effective_risk_profile

    monkeypatch.setenv("SYGNIF_SWARM_RISK_PROFILE", "demo_safe")
    assert resolve_effective_risk_profile("default") == "default"
    assert resolve_effective_risk_profile(None) == "demo_safe"
    monkeypatch.delenv("SYGNIF_SWARM_RISK_PROFILE", raising=False)
    assert resolve_effective_risk_profile(None) == "default"


def test_resolve_invalid_env_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    from finance_agent.swarm_risk_profile import resolve_effective_risk_profile

    monkeypatch.setenv("SYGNIF_SWARM_RISK_PROFILE", "not-a-real-profile")
    assert resolve_effective_risk_profile(None) == "default"


def test_merge_loop_defaults_reads_predict_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SYGNIF_PREDICT_DEFAULT_NOTIONAL_USDT", "7777")
    monkeypatch.setenv("SYGNIF_PREDICT_DEFAULT_MANUAL_LEVERAGE", "8")
    monkeypatch.setenv("SYGNIF_PREDICT_OPEN_IMMEDIATE", "0")
    mod = _load_swarm_auto_module()
    out = mod._merge_loop_defaults(["--execute"])
    joined = " ".join(out)
    assert "--manual-notional-usdt 7777" in joined
    assert "--manual-leverage 8" in joined
    assert "--interval-sec 300" in joined
