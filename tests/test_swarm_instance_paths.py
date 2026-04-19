from __future__ import annotations

import os
from pathlib import Path

import pytest


@pytest.fixture()
def fake_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("HOME", str(tmp_path))
    return tmp_path


def test_instance_roots_respects_link_off(fake_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SYGNIF_SWARM_LINK_INSTANCE", "0")
    repo = fake_home / "SYGNIF"
    repo.mkdir()
    (fake_home / "truthcoin-dc").mkdir()
    from finance_agent.swarm_instance_paths import instance_roots

    assert instance_roots(repo) == []


def test_explicit_instance_roots(fake_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SYGNIF_SWARM_LINK_INSTANCE", "1")
    a = fake_home / "proj_a"
    b = fake_home / "proj_b"
    a.mkdir()
    b.mkdir()
    repo = fake_home / "SYGNIF"
    repo.mkdir()
    monkeypatch.setenv("SYGNIF_INSTANCE_ROOTS", f"{a}:{b}")
    from finance_agent.swarm_instance_paths import instance_roots

    got = instance_roots(repo)
    assert got == [a.resolve(), b.resolve()]


def test_apply_swarm_instance_env_order(fake_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """xrp wins over repo on duplicate keys; sibling fills only missing."""
    monkeypatch.setenv("SYGNIF_SWARM_LINK_INSTANCE", "1")
    monkeypatch.setenv("SYGNIF_INSTANCE_ROOTS", "")

    repo = fake_home / "SYGNIF"
    repo.mkdir()
    (fake_home / "truthcoin-dc").mkdir()
    (fake_home / "xrp_claude_bot").mkdir()

    (fake_home / "truthcoin-dc" / ".env").write_text(
        "ONLY_SIBLING=1\n"
        "DUP=from_sibling\n",
        encoding="utf-8",
    )
    (fake_home / "xrp_claude_bot" / ".env").write_text("DUP=from_xrp\n", encoding="utf-8")
    (repo / ".env").write_text("DUP=from_repo\nONLY_REPO=2\n", encoding="utf-8")

    monkeypatch.delenv("SYGNIF_SECRETS_ENV_FILE", raising=False)
    from finance_agent.swarm_instance_paths import apply_swarm_instance_env

    apply_swarm_instance_env(repo)

    assert os.environ.get("ONLY_SIBLING") == "1"
    assert os.environ.get("ONLY_REPO") == "2"
    assert os.environ.get("DUP") == "from_xrp"

    (repo / "swarm_operator.env").write_text("DUP=from_operator\n", encoding="utf-8")
    apply_swarm_instance_env(repo)
    assert os.environ.get("DUP") == "from_operator"
