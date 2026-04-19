"""
**NeuroLinked ↔ SYGNIF Swarm (BTC)** bridge.

Vendored NeuroLinked lives under ``SYGNIF/third_party/neurolinked`` (see ``sygnif_swarm_bridge.py``).
This module **does not** place Bybit orders; it only reads ``swarm_knowledge.compute_swarm()`` and
feeds a **text-derived feature vector** into NeuroLinked's existing ``Brain.inject_sensory_input("text", …)``.

Optional side-channel JSON for dashboards or MCP glue:
``prediction_agent/neurolinked_swarm_channel.json`` (atomic write, overridable via env).
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Callable

_JSON_WRITE_DEFAULT = str


def _repo_root(repo: Path | None) -> Path:
    return repo or Path(__file__).resolve().parents[1]


def neurolinked_root(repo: Path | None = None) -> Path:
    return _repo_root(repo) / "third_party" / "neurolinked"


def _ensure_sygnif_pythonpath(repo: Path) -> None:
    fa = str(repo / "finance_agent")
    pa = str(repo / "prediction_agent")
    for p in (fa, pa):
        if p not in sys.path:
            sys.path.insert(0, p)


def _ensure_neurolinked_path(repo: Path) -> Path:
    nl = neurolinked_root(repo)
    if not (nl / "brain" / "brain.py").is_file():
        raise FileNotFoundError(
            f"NeuroLinked checkout missing under {nl} — unzip NeuroLinked-V1.2-SOURCE there or set path."
        )
    s = str(nl)
    if s not in sys.path:
        sys.path.insert(0, s)
    return nl


def _import_text_encoder(repo: Path):
    _ensure_neurolinked_path(repo)
    from sensory.text import TextEncoder  # noqa: PLC0415

    return TextEncoder


def channel_json_path(repo: Path | None = None) -> Path:
    raw = (os.environ.get("SYGNIF_NEUROLINKED_SWARM_CHANNEL_JSON") or "").strip()
    if raw:
        return Path(raw).expanduser()
    return _repo_root(repo) / "prediction_agent" / "neurolinked_swarm_channel.json"


def swarm_obsidian_rel_path() -> str:
    """Relative path inside the vault for the live Swarm mirror note."""
    rel = (os.environ.get("NEUROLINKED_SWARM_OBSIDIAN_REL") or "Sygnif/Swarm-Live.md").strip()
    return rel.lstrip("/\\")


def format_swarm_obsidian_markdown(sw: dict[str, Any]) -> str:
    """
    Human-readable Obsidian note + machine block (``format_swarm_for_neurolinked``).

    Frontmatter ``sygnif_neurolinked_skip_index: true`` tells NeuroLinked's vault scanner
    not to duplicate this file into ``KnowledgeStore`` (Swarm already stores ``sygnif_swarm``).
    """
    from datetime import datetime, timezone

    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    label = sw.get("swarm_label")
    mean = sw.get("swarm_mean")
    conflict = sw.get("swarm_conflict")
    n_src = sw.get("sources_n")
    raw_block = format_swarm_for_neurolinked(sw).rstrip()

    rows: list[str] = []
    src = sw.get("sources") if isinstance(sw.get("sources"), dict) else {}
    if isinstance(src, dict):
        for name in sorted(src.keys()):
            blob = src.get(name)
            if not isinstance(blob, dict):
                continue
            vote = blob.get("vote")
            detail = str(blob.get("detail") or "").replace("|", "\\|").replace("\n", " ")[:120]
            rows.append(f"| `{name}` | {vote!s} | {detail} |")

    table = "\n".join(rows) if rows else "| — | — | (no sources) |"
    fence = "```"

    fm = f"""---
sygnif_neurolinked_skip_index: true
sygnif_swarm_mirror: true
swarm_label: {json.dumps(label, ensure_ascii=False)}
swarm_mean: {json.dumps(mean, default=_JSON_WRITE_DEFAULT)}
swarm_conflict: {json.dumps(bool(conflict))}
sources_n: {json.dumps(n_src, default=_JSON_WRITE_DEFAULT)}
updated: {json.dumps(ts)}
---

# SYGNIF Swarm (live)

**Label:** {label!s} · **Mean:** {mean!s} · **Conflict:** {conflict!s} · **Sources:** {n_src!s} · **Updated (UTC):** `{ts}`

## Source votes

| Source | Vote | Detail |
| --- | ---: | --- |
{table}

## Machine lines (NeuroLinked encoder)

The block below is the same line-oriented feed used for the neuromorphic text pathway.

{fence}
{raw_block}
{fence}
"""
    return fm


def write_swarm_obsidian_mirror(
    sw: dict[str, Any],
    vault_root: str | Path,
    rel_path: str | None = None,
) -> Path | None:
    """
    Atomically write ``format_swarm_obsidian_markdown(sw)`` into the vault.

    Returns the written path, or ``None`` if the vault directory is missing.
    """
    vault = Path(vault_root).expanduser().resolve()
    if not vault.is_dir():
        return None
    rel = rel_path or swarm_obsidian_rel_path()
    out = vault / rel
    out.parent.mkdir(parents=True, exist_ok=True)
    content = format_swarm_obsidian_markdown(sw)
    tmp = out.with_suffix(out.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(out)
    return out


def format_swarm_for_neurolinked(sw: dict[str, Any]) -> str:
    """Stable, line-oriented text for ``TextEncoder`` (no secrets)."""
    lines: list[str] = []
    lines.append(f"SYGNIF_SWARM_LABEL {sw.get('swarm_label')!s}")
    lines.append(f"SYGNIF_SWARM_MEAN {sw.get('swarm_mean')!s}")
    lines.append(f"SYGNIF_SWARM_CONFLICT {sw.get('swarm_conflict')!s}")
    lines.append(f"SYGNIF_SWARM_SOURCES_N {sw.get('sources_n')!s}")
    src = sw.get("sources") if isinstance(sw.get("sources"), dict) else {}
    if isinstance(src, dict):
        for name in sorted(src.keys()):
            blob = src.get(name)
            if not isinstance(blob, dict):
                continue
            v = blob.get("vote")
            d = str(blob.get("detail") or "")[:160].replace("\n", " ")
            lines.append(f"SYGNIF_SWARM_SRC {name} vote={v} detail={d}")
    hm = sw.get("hivemind_explore") if isinstance(sw.get("hivemind_explore"), dict) else {}
    cs = hm.get("consensus_summary") if isinstance(hm.get("consensus_summary"), dict) else {}
    if cs:
        lines.append(
            "HIVEMIND_CONSENSUS "
            f"tc_slots={cs.get('slots_voting_n')!s} tc_mkts={cs.get('markets_trading_n')!s} "
            f"bybit24h_pct={cs.get('bybit_24h_pct')!s} bybit_hint={cs.get('bybit_direction_vote_hint')!s} "
            f"spread_bps={cs.get('spread_bps')!s}"
        )
    br = hm.get("bybit_reference") if isinstance(hm.get("bybit_reference"), dict) else {}
    if br:
        vol = str(br.get("volume24h") or "")[:28]
        tov = str(br.get("turnover24h") or "")[:28]
        oi = str(br.get("openInterestValue") or "")[:20]
        lines.append(
            "BYBIT_HIVEMIND_LAYER "
            f"vol24h={vol} turnover24h={tov} oi={oi} spread_bps={br.get('spread_bps')!s}"
        )
    lines.append("SYGNIF_SWARM_END")
    return "\n".join(lines) + "\n"


def write_swarm_channel_json(
    sw: dict[str, Any],
    *,
    repo: Path | None = None,
    extra: dict[str, Any] | None = None,
) -> Path:
    """Atomic JSON snapshot for operators / external consumers."""
    path = channel_json_path(repo)
    path.parent.mkdir(parents=True, exist_ok=True)
    out: dict[str, Any] = {
        "schema": "sygnif.neurolinked_swarm_channel/v1",
        "swarm_mean": sw.get("swarm_mean"),
        "swarm_label": sw.get("swarm_label"),
        "swarm_conflict": sw.get("swarm_conflict"),
        "sources_n": sw.get("sources_n"),
    }
    if extra:
        out["extra"] = extra
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(out, indent=2, ensure_ascii=False, default=_JSON_WRITE_DEFAULT) + "\n", encoding="utf-8")
    tmp.replace(path)
    return path


class NeurolinkedSwarmBridge:
    """
    Pull SYGNIF Swarm knowledge → encode → NeuroLinked ``text`` sensory queue.

    ``compute_fn`` defaults to ``swarm_knowledge.compute_swarm`` after ``apply_swarm_instance_env``.
    """

    def __init__(
        self,
        repo_root: Path | None = None,
        *,
        feature_dim: int = 256,
        compute_fn: Callable[[], dict[str, Any]] | None = None,
        extra_env_file: Path | None = None,
    ) -> None:
        self.repo = _repo_root(repo_root)
        self.feature_dim = feature_dim
        self._compute_fn = compute_fn
        self._extra_env_file = extra_env_file
        self._encoder = None

    def _get_encoder(self):
        if self._encoder is None:
            Enc = _import_text_encoder(self.repo)
            self._encoder = Enc(self.feature_dim)
        return self._encoder

    def pull_swarm(self) -> dict[str, Any]:
        _ensure_sygnif_pythonpath(self.repo)
        try:
            from swarm_instance_paths import apply_swarm_instance_env  # noqa: PLC0415
        except ImportError:
            apply_swarm_instance_env = None  # type: ignore[assignment]
        if apply_swarm_instance_env is not None:
            envf = self._extra_env_file
            if envf is None:
                cand = self.repo / "swarm_operator.env"
                envf = cand if cand.is_file() else None
            apply_swarm_instance_env(self.repo, extra_env_file=envf)
        if self._compute_fn is not None:
            return self._compute_fn()
        import swarm_knowledge as sk  # noqa: PLC0415

        return sk.compute_swarm()

    def inject_into_brain(
        self,
        brain: Any,
        *,
        write_channel: bool = True,
        knowledge_store: Any | None = None,
    ) -> dict[str, Any]:
        """
        ``brain`` is a NeuroLinked ``brain.brain.Brain`` instance.

        When ``knowledge_store`` is a NeuroLinked ``KnowledgeStore``, append one
        ``store()`` row per tick (source ``sygnif_swarm``) for MCP / recall.
        """
        sw = self.pull_swarm()
        text = format_swarm_for_neurolinked(sw)
        vec = self._get_encoder().encode(text)
        brain.inject_sensory_input("text", vec, executive_boost=True)
        meta: dict[str, Any] = {
            "ok": True,
            "swarm_label": sw.get("swarm_label"),
            "swarm_mean": sw.get("swarm_mean"),
            "text_chars": len(text),
            "feature_dim": int(vec.shape[0]) if hasattr(vec, "shape") else None,
        }

        vault = (os.environ.get("NEUROLINKED_OBSIDIAN_VAULT") or "").strip()
        write_obs = (os.environ.get("NEUROLINKED_SWARM_OBSIDIAN_WRITE") or "1").strip().lower()
        if vault and write_obs not in ("0", "false", "no", "off"):
            try:
                note_path = write_swarm_obsidian_mirror(sw, vault, swarm_obsidian_rel_path())
                if note_path is not None:
                    meta["obsidian_swarm_note"] = str(note_path)
            except OSError as e:
                meta["obsidian_swarm_note_error"] = str(e)
        if knowledge_store is not None and hasattr(knowledge_store, "store"):
            tags = ["sygnif", "swarm", "btc", str(sw.get("swarm_label") or "unknown").lower()]
            kid = int(
                knowledge_store.store(
                    text,
                    source="sygnif_swarm",
                    tags=tags,
                    metadata={"swarm_mean": sw.get("swarm_mean"), "sources_n": sw.get("sources_n")},
                )
            )
            meta["knowledge_id"] = kid
        if write_channel:
            write_swarm_channel_json(sw, repo=self.repo, extra={"neurolinked": True})
            meta["channel_json"] = str(channel_json_path(self.repo))
        return meta
