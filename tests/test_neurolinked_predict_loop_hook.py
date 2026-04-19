"""``neurolinked_predict_loop_hook`` — Swarm → JSON + NeuroLinked HTTP."""
from __future__ import annotations

from pathlib import Path
from unittest import mock

import pytest

REPO = Path(__file__).resolve().parent.parent


def test_hook_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    from finance_agent import neurolinked_predict_loop_hook as h

    monkeypatch.delenv("SYGNIF_NEUROLINKED_SWARM_HOOK", raising=False)
    out = h.push_neurolinked_network(REPO, 1, {"swarm_label": "X"})
    assert out.get("skipped") is True
    assert "off" in str(out.get("reason", ""))


def test_hook_writes_and_posts(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from finance_agent import neurolinked_predict_loop_hook as h

    monkeypatch.setenv("SYGNIF_NEUROLINKED_SWARM_HOOK", "1")
    monkeypatch.setenv("SYGNIF_NEUROLINKED_SWARM_HOOK_EVERY_N", "1")
    monkeypatch.setenv("SYGNIF_NEUROLINKED_HTTP_URL", "http://example.invalid")
    monkeypatch.setenv("SYGNIF_NEUROLINKED_SWARM_CHANNEL_JSON", str(tmp_path / "ch.json"))

    class Resp:
        def getcode(self) -> int:
            return 200

        def read(self) -> bytes:
            return b'{"status":"ok","encoded_dim":256}'

        def __enter__(self) -> "Resp":
            return self

        def __exit__(self, *a: object) -> None:
            return None

    with mock.patch("finance_agent.neurolinked_predict_loop_hook.urllib.request.urlopen", return_value=Resp()):
        out = h.push_neurolinked_network(
            tmp_path,
            2,
            {"swarm_label": "BULLISH", "swarm_mean": 0.1, "swarm_conflict": False, "sources_n": 0, "sources": {}},
            predict_meta={"iter": 2, "target_side": "long"},
        )

    assert out.get("skipped") is False
    assert out.get("http_status") == 200
    assert (tmp_path / "ch.json").is_file()


def test_hook_every_n_skip(monkeypatch: pytest.MonkeyPatch) -> None:
    from finance_agent import neurolinked_predict_loop_hook as h

    monkeypatch.setenv("SYGNIF_NEUROLINKED_SWARM_HOOK", "1")
    monkeypatch.setenv("SYGNIF_NEUROLINKED_SWARM_HOOK_EVERY_N", "3")
    out = h.push_neurolinked_network(REPO, 2, {"swarm_label": "X"})
    assert out.get("reason") == "every_n_skip"
