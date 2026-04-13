"""Tests for optional NewHedge client (no live API key required)."""


def test_format_telegram_skips_without_key(monkeypatch):
    monkeypatch.delenv("NEWHEDGE_API_KEY", raising=False)
    from finance_agent.newhedge_client import format_telegram_altcoins_correlation_block

    assert format_telegram_altcoins_correlation_block() == ""


def test_format_telegram_summarizes_series(monkeypatch):
    monkeypatch.setenv("NEWHEDGE_API_KEY", "dummy")
    from finance_agent import newhedge_client as nh

    fake = [[[0, 0.1], [1_700_000_000_000, 0.55]], None]

    def _fake_fetch(*, api_key=None, timeout_sec=20.0):
        return fake[0], fake[1]

    monkeypatch.setattr(nh, "fetch_altcoins_correlation_usd", _fake_fetch)
    s = nh.format_telegram_altcoins_correlation_block()
    assert "0.55" in s
    assert "not Sygnif TA" in s or "not Sygnif" in s
