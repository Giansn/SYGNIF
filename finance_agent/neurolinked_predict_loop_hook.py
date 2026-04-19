"""
Predict-loop hook: **SYGNIF Swarm → NeuroLinked network** (HTTP + JSON channel).

When ``SYGNIF_NEUROLINKED_SWARM_HOOK=1``:

1. Writes ``prediction_agent/neurolinked_swarm_channel.json`` (via ``neurolinked_swarm_adapter``) with Swarm + predict meta.
2. If ``SYGNIF_NEUROLINKED_HTTP_URL`` is non-empty (default ``http://127.0.0.1:8889``), ``POST``s the same text
   bundle to NeuroLinked ``/api/input/text`` so a running ``run.py`` dashboard brain receives it.

No order placement; failures are non-fatal (logged only).
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

try:
    from neurolinked_swarm_adapter import (  # noqa: PLC0415
        format_swarm_for_neurolinked,
        write_swarm_channel_json,
    )
except ImportError:
    from finance_agent.neurolinked_swarm_adapter import (  # noqa: PLC0415
        format_swarm_for_neurolinked,
        write_swarm_channel_json,
    )


def _env_truthy(name: str, *, default: bool = False) -> bool:
    raw = (os.environ.get(name) or "").strip().lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on")


def _every_n() -> int:
    raw = (os.environ.get("SYGNIF_NEUROLINKED_SWARM_HOOK_EVERY_N") or "1").strip() or "1"
    try:
        n = int(float(raw))
    except ValueError:
        n = 1
    return max(1, n)


def _http_url() -> str:
    return (os.environ.get("SYGNIF_NEUROLINKED_HTTP_URL") or "http://127.0.0.1:8889").strip().rstrip("/")


def _http_timeout_sec() -> float:
    # Default 15s: predict-loop POST can queue behind summary/WS; Neurolinked ingest is fast
    # once SYGNIF_SWARM* skips the heavy ClaudeBridge path (see third_party/neurolinked/server.py).
    raw = (os.environ.get("SYGNIF_NEUROLINKED_HTTP_TIMEOUT_SEC") or "15").strip() or "15"
    try:
        return max(0.25, min(60.0, float(raw)))
    except ValueError:
        return 3.0


def _predict_meta_lines(meta: dict[str, Any] | None) -> str:
    if not meta:
        return ""
    keys = (
        "iter",
        "ts_utc",
        "target_side",
        "swarm_gate_ok",
        "swarm_reason",
        "entry_blocked",
        "enhanced",
        "predict_ms",
    )
    lines = []
    for k in keys:
        if k in meta and meta[k] is not None:
            lines.append(f"SYGNIF_PREDICT_LOOP {k}={meta[k]!s}")
    return ("\n".join(lines) + "\n") if lines else ""


def push_neurolinked_network(
    repo: Path,
    iter_count: int,
    swarm: dict[str, Any] | None,
    *,
    predict_meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Full network push: **JSON channel file** + optional **NeuroLinked HTTP** ingest.

    If ``swarm`` is ``None``, calls ``compute_swarm()`` (extra work — prefer passing
    the snapshot from the Swarm gate path when available).
    """
    out: dict[str, Any] = {"ok": True, "skipped": True}
    if not _env_truthy("SYGNIF_NEUROLINKED_SWARM_HOOK"):
        out["reason"] = "SYGNIF_NEUROLINKED_SWARM_HOOK_off"
        return out
    if iter_count % _every_n() != 0:
        out["reason"] = "every_n_skip"
        out["skipped"] = True
        return out

    sw = swarm
    if sw is None:
        try:
            from swarm_instance_paths import apply_swarm_instance_env  # noqa: PLC0415
        except ImportError:
            from finance_agent.swarm_instance_paths import apply_swarm_instance_env  # noqa: PLC0415
        try:
            from swarm_knowledge import compute_swarm  # noqa: PLC0415
        except ImportError:
            from finance_agent.swarm_knowledge import compute_swarm  # noqa: PLC0415
        envf = repo / "swarm_operator.env"
        apply_swarm_instance_env(repo, extra_env_file=envf if envf.is_file() else None)
        sw = compute_swarm()

    extra = {"iter": iter_count, "neurolinked_hook": True}
    if predict_meta:
        extra["predict_loop"] = {k: predict_meta.get(k) for k in predict_meta if k in predict_meta}

    path = write_swarm_channel_json(sw, repo=repo, extra=extra)
    text = format_swarm_for_neurolinked(sw) + _predict_meta_lines(predict_meta)
    out["skipped"] = False
    out["channel_json"] = str(path)
    out["text_chars"] = len(text)

    base = _http_url()
    if not base:
        out["http"] = "skipped_no_url"
        return out

    url = f"{base}/api/input/text"
    body = json.dumps(
        {
            "text": text,
            "skip_claude_bridge": True,
            "skip_sygnif_bridge": True,
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=_http_timeout_sec()) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            out["http_status"] = resp.getcode()
            out["http"] = "ok" if int(resp.getcode() or 0) < 400 else "http_error"
            try:
                out["http_body"] = json.loads(raw)
            except json.JSONDecodeError:
                out["http_body"] = raw[:500]
    except urllib.error.HTTPError as exc:
        out["http"] = "error"
        out["http_error"] = f"HTTP {exc.code}"
        try:
            out["http_detail"] = (exc.read() or b"").decode("utf-8", errors="replace")[:300]
        except Exception:
            pass
    except urllib.error.URLError as exc:
        out["http"] = "error"
        out["http_error"] = str(exc.reason)[:200] if getattr(exc, "reason", None) else str(exc)[:200]
    except Exception as exc:  # noqa: BLE001
        out["http"] = "error"
        out["http_error"] = str(exc)[:200]

    return out
