"""scripts/swarm_demo_pnl_report.py — summary helpers."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]


def _load_demo_pnl_mod():
    path = _REPO / "scripts" / "swarm_demo_pnl_report.py"
    spec = importlib.util.spec_from_file_location("_sdpn_test", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["_sdpn_test"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_compact_closed_pnl_summary_ok() -> None:
    mod = _load_demo_pnl_mod()

    rep = {
        "enabled": True,
        "ok": True,
        "venue": "demo",
        "symbol": "BTCUSDT",
        "n_closed": 10,
        "sum_closed_pnl_usdt": -1.5,
        "wins": 3,
        "losses": 7,
        "recent": [],
    }
    c = mod.compact_closed_pnl_summary(rep)
    assert c["ok"] is True
    assert c["wins"] == 3
    assert "recent" not in c


def test_compact_closed_pnl_summary_not_ok() -> None:
    mod = _load_demo_pnl_mod()

    c = mod.compact_closed_pnl_summary(
        {"enabled": True, "ok": False, "venue": "demo", "symbol": "BTCUSDT", "detail": "no_keys"}
    )
    assert c["ok"] is False
    assert c["detail"] == "no_keys"


def test_text_report_formats() -> None:
    mod = _load_demo_pnl_mod()

    assert "disabled" in mod.text_report({"enabled": False})
    assert "ok=false" in mod.text_report({"enabled": True, "ok": False, "detail": "x"})
    body = mod.text_report(
        {
            "enabled": True,
            "ok": True,
            "venue": "demo",
            "symbol": "BTCUSDT",
            "n_closed": 2,
            "sum_closed_pnl_usdt": 0.5,
            "wins": 1,
            "losses": 1,
            "recent": [{"closed_pnl": 0.5, "side": "Sell", "qty": 0.01, "avg_entry": 1.0, "avg_exit": 2.0}],
        }
    )
    assert "sum_closed_pnl_usdt=0.5" in body
    assert "pnl=0.5" in body


def test_script_help_runs() -> None:
    script = _REPO / "scripts" / "swarm_demo_pnl_report.py"
    p = subprocess.run(
        [sys.executable, str(script), "--help"],
        cwd=str(_REPO),
        capture_output=True,
        text=True,
        check=False,
    )
    assert p.returncode == 0
    assert "--max-rows" in p.stdout


def test_main_strict_exit_code(monkeypatch: pytest.MonkeyPatch) -> None:
    """``--strict`` returns 1 when ``build_bybit_closed_pnl_report`` yields ok=false."""
    mod = _load_demo_pnl_mod()
    fa = str(_REPO / "finance_agent")
    if fa not in sys.path:
        sys.path.insert(0, fa)
    import swarm_instance_paths as sip  # noqa: PLC0415
    import swarm_knowledge as sk  # noqa: PLC0415

    monkeypatch.setattr(sip, "apply_swarm_instance_env", lambda *_a, **_k: None)

    def _bad_report() -> dict:
        return {"enabled": True, "ok": False, "venue": "demo", "symbol": "BTCUSDT", "detail": "unit_test"}

    monkeypatch.setattr(sk, "build_bybit_closed_pnl_report", _bad_report)
    monkeypatch.setattr(sys, "argv", ["swarm_demo_pnl_report.py", "--strict"])
    assert mod.main() == 1

    monkeypatch.setattr(sk, "build_bybit_closed_pnl_report", lambda: {"enabled": True, "ok": True, "venue": "demo", "symbol": "X", "n_closed": 0, "sum_closed_pnl_usdt": 0.0, "wins": 0, "losses": 0, "recent": []})
    monkeypatch.setattr(sys, "argv", ["swarm_demo_pnl_report.py", "--strict"])
    assert mod.main() == 0
