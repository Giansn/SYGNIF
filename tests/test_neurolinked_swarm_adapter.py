"""``neurolinked_swarm_adapter`` — Swarm text formatting + brain inject (mocked)."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

REPO = Path(__file__).resolve().parent.parent


def test_format_swarm_for_neurolinked() -> None:
    from finance_agent import neurolinked_swarm_adapter as nsa

    text = nsa.format_swarm_for_neurolinked(
        {
            "swarm_label": "BULLISH",
            "swarm_mean": 0.12,
            "swarm_conflict": False,
            "sources_n": 2,
            "sources": {"bf": {"vote": 1, "detail": "posL"}, "hm": {"vote": 0, "detail": "quiet"}},
        }
    )
    assert "BULLISH" in text
    assert "SYGNIF_SWARM_SRC bf" in text


def test_inject_into_brain_mock(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from finance_agent import neurolinked_swarm_adapter as nsa

    nl = tmp_path / "third_party" / "neurolinked"
    (nl / "brain").mkdir(parents=True)
    (nl / "sensory").mkdir(parents=True)
    (nl / "brain" / "brain.py").write_text("# stub\n", encoding="utf-8")
    (nl / "sensory" / "text.py").write_text(
        "import numpy as np\n"
        "class TextEncoder:\n"
        "    def __init__(self, feature_dim=256):\n"
        "        self.feature_dim = feature_dim\n"
        "    def encode(self, text):\n"
        "        return np.ones(self.feature_dim, dtype=np.float32) * 0.5\n",
        encoding="utf-8",
    )

    captured: dict[str, object] = {}

    class DummyBrain:
        def inject_sensory_input(
            self, modality: str, features: np.ndarray, **kwargs: object
        ) -> None:
            captured["modality"] = modality
            captured["features"] = features
            captured["kwargs"] = kwargs

    def fake_compute() -> dict:
        return {
            "swarm_label": "MIXED",
            "swarm_mean": 0.0,
            "swarm_conflict": True,
            "sources_n": 1,
            "sources": {},
        }

    bridge = nsa.NeurolinkedSwarmBridge(tmp_path, compute_fn=fake_compute)
    meta = bridge.inject_into_brain(DummyBrain(), write_channel=False)
    assert meta["ok"] is True
    assert captured.get("modality") == "text"
    assert isinstance(captured.get("features"), np.ndarray)
    assert captured.get("kwargs", {}).get("executive_boost") is True


def test_format_swarm_obsidian_markdown_skip_flag() -> None:
    from finance_agent import neurolinked_swarm_adapter as nsa

    md = nsa.format_swarm_obsidian_markdown(
        {
            "swarm_label": "X",
            "swarm_mean": 0.5,
            "swarm_conflict": False,
            "sources_n": 1,
            "sources": {"a": {"vote": 1, "detail": "ok"}},
        }
    )
    assert "sygnif_neurolinked_skip_index: true" in md
    assert "SYGNIF_SWARM_LABEL" in md


def test_inject_writes_obsidian_mirror(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from finance_agent import neurolinked_swarm_adapter as nsa

    vault = tmp_path / "vault"
    vault.mkdir()
    monkeypatch.setenv("NEUROLINKED_OBSIDIAN_VAULT", str(vault))
    monkeypatch.setenv("NEUROLINKED_SWARM_OBSIDIAN_REL", "Inbox/Swarm.md")

    nl = tmp_path / "third_party" / "neurolinked"
    (nl / "brain").mkdir(parents=True)
    (nl / "sensory").mkdir(parents=True)
    (nl / "brain" / "brain.py").write_text("# stub\n", encoding="utf-8")
    (nl / "sensory" / "text.py").write_text(
        "import numpy as np\n"
        "class TextEncoder:\n"
        "    def __init__(self, feature_dim=256):\n"
        "        self.feature_dim = feature_dim\n"
        "    def encode(self, text):\n"
        "        return np.ones(self.feature_dim, dtype=np.float32) * 0.5\n",
        encoding="utf-8",
    )

    class DummyBrain:
        def inject_sensory_input(self, modality: str, features: np.ndarray, **kwargs: object) -> None:
            pass

    def fake_compute() -> dict:
        return {
            "swarm_label": "MIXED",
            "swarm_mean": 0.0,
            "swarm_conflict": False,
            "sources_n": 0,
            "sources": {},
        }

    bridge = nsa.NeurolinkedSwarmBridge(tmp_path, compute_fn=fake_compute)
    meta = bridge.inject_into_brain(DummyBrain(), write_channel=False)
    note = vault / "Inbox" / "Swarm.md"
    assert meta.get("obsidian_swarm_note") == str(note.resolve())
    assert note.is_file()
    body = note.read_text(encoding="utf-8")
    assert "sygnif_neurolinked_skip_index: true" in body


def test_swarm_obsidian_mirror_skipped_in_vault_sync(tmp_path: Path) -> None:
    """Mirrored Swarm note must not duplicate rows in ``KnowledgeStore``."""
    from finance_agent import neurolinked_swarm_adapter as nsa

    vault = tmp_path / "vault"
    vault.mkdir()
    sw = {
        "swarm_label": "BULL",
        "swarm_mean": 1.0,
        "swarm_conflict": False,
        "sources_n": 0,
        "sources": {},
    }
    nsa.write_swarm_obsidian_mirror(sw, vault, "Swarm-Live.md")

    for k in list(sys.modules.keys()):
        if k == "sensory" or k.startswith("sensory."):
            del sys.modules[k]
    nl_root = str(REPO / "third_party" / "neurolinked")
    if nl_root in sys.path:
        sys.path.remove(nl_root)
    sys.path.insert(0, nl_root)
    from brain.knowledge_store import KnowledgeStore  # noqa: PLC0415
    from sensory.obsidian_vault import sync_obsidian_vault_once  # noqa: PLC0415

    ks = KnowledgeStore(db_path=str(tmp_path / "k.db"))
    r = sync_obsidian_vault_once(str(vault), knowledge_store=ks, brain=None, text_encoder=None)
    assert r.get("stored") == 0
    assert r.get("skipped_mirror") == 1
