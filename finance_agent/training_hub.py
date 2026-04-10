"""
Orthogonal / nn-zero-to-hero training discovery for finance-agent HTTP.

Exposes GET /training and GET /training/status so operators and automations can
find Jupyter and repo paths. Does not run training or load model weights.

See requirements-training.txt (same directory) for optional torch/orthogonium pins used
in the nn-zero-to-hero venv, not in production finance-agent.
"""
from __future__ import annotations

import html
import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


def orthogonal_training_base_url() -> str:
    """Base URL for Jupyter (no trailing slash)."""
    explicit = (os.environ.get("ORTHOGONAL_TRAINING_BASE") or "").strip().rstrip("/")
    if explicit:
        return explicit
    host = (os.environ.get("ORTHOGONAL_TRAINING_HOST") or "127.0.0.1").strip()
    port = int(os.environ.get("ORTHOGONAL_TRAINING_PORT", "8890"))
    return f"http://{host}:{port}"


def _expand_path(key: str, default: str) -> str:
    raw = (os.environ.get(key) or default).strip()
    return str(Path(raw).expanduser().resolve())


def probe_orthogonal_jupyter(base_url: str, timeout: float = 2.5) -> dict[str, Any]:
    """HEAD/GET Jupyter root; treat HTTP errors that imply a live server as reachable."""
    url = base_url.rstrip("/") + "/"
    try:
        req = urllib.request.Request(url, method="GET", headers={"User-Agent": "finance-agent-training-probe/1"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return {"reachable": True, "http_status": resp.getcode(), "error": None}
    except urllib.error.HTTPError as e:
        if e.code in (401, 403, 302, 301):
            return {"reachable": True, "http_status": e.code, "error": None}
        return {"reachable": False, "http_status": e.code, "error": e.reason}
    except OSError as e:
        return {"reachable": False, "http_status": None, "error": str(e)}


def build_training_payload() -> dict[str, Any]:
    base = orthogonal_training_base_url()
    probe = probe_orthogonal_jupyter(base)
    nn_root = _expand_path("NN_ZERO_TO_HERO_ROOT", "~/nn-zero-to-hero")
    sygnif = _expand_path("SYGNIF_REPO", "~/SYGNIF")
    lectures = str(Path(nn_root) / "lectures")

    out: dict[str, Any] = {
        "ok": True,
        "orthogonal_course": "https://github.com/karpathy/nn-zero-to-hero",
        "jupyter": {
            "base_url": base,
            "reachable": probe["reachable"],
            "http_status": probe["http_status"],
            "probe_error": probe["error"],
        },
        "paths": {
            "nn_zero_to_hero": nn_root,
            "lectures": lectures,
            "sygnif_repo": sygnif,
        },
        "ssh_tunnel_example": "ssh -L 8890:127.0.0.1:8890 ubuntu@<this-host>",
        "start_jupyter_command": f"cd {nn_root} && ./start-jupyter.sh",
        "finance_agent_http": {
            "training_overview": "/training?format=html",
            "training_json": "/training",
            "training_status": "/training/status",
        },
        "docker_hint": (
            "Inside the finance-agent container, set ORTHOGONAL_TRAINING_HOST=host.docker.internal "
            "(see docker-compose) so /training/status can reach Jupyter on the host."
        ),
        "optional_training_requirements": str(
            Path(__file__).resolve().parent / "requirements-training.txt"
        ),
        "integration_note": (
            "Optional numpy MLP blends with rules when SENTIMENT_MLP_WEIGHTS is set; retrain with "
            "scripts/train_sentiment_mlp.py (synthetic + optional --freqtrade-db closed trades). "
            "Analyze DBs: scripts/analyze_closed_trades.py. "
            "Alongside the nn-zero-to-hero course, Orthogonium-backed orthogonal / 1-Lipschitz experiments: "
            "install optional deps via optional_training_requirements "
            "(same install line as in requirements-training.txt: activate nn-zero-to-hero .venv, pip install -r path from optional_training_requirements); "
            "pin package versions when exporting serialized weights (Orthogonium README)."
        ),
    }
    return out


def training_status_payload() -> dict[str, Any]:
    """Compact JSON for health dashboards."""
    base = orthogonal_training_base_url()
    probe = probe_orthogonal_jupyter(base)
    return {
        "ok": True,
        "jupyter_base_url": base,
        "jupyter_reachable": probe["reachable"],
        "http_status": probe["http_status"],
        "error": probe["error"],
    }


def training_page_html() -> str:
    payload = build_training_payload()
    p = json.dumps(payload, indent=2)
    safe_json = html.escape(p)
    base = html.escape(str(payload["jupyter"]["base_url"]))
    nn_root = html.escape(str(payload["paths"]["nn_zero_to_hero"]))
    lectures = html.escape(str(payload["paths"]["lectures"]))
    reachable = payload["jupyter"]["reachable"]
    status_badge = "reachable" if reachable else "not reachable (start Jupyter or fix ORTHOGONAL_TRAINING_*)"

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>Finance agent — orthogonal training</title>
  <style>
    body {{ font-family: system-ui, sans-serif; max-width: 52rem; margin: 1.5rem auto; padding: 0 1rem; line-height: 1.5; }}
    code, pre {{ background: #f4f4f5; padding: 0.2em 0.4em; border-radius: 4px; font-size: 0.9em; }}
    pre {{ padding: 1rem; overflow: auto; }}
    .ok {{ color: #166534; }} .warn {{ color: #a16207; }}
    h1 {{ font-size: 1.25rem; }}
    ul {{ padding-left: 1.2rem; }}
  </style>
</head>
<body>
  <h1>Finance agent ↔ orthogonal (nn-zero-to-hero) training</h1>
  <p class="{'ok' if reachable else 'warn'}">Jupyter probe: <strong>{html.escape(status_badge)}</strong></p>
  <p>Course notebooks live under <code>{lectures}</code>. Jupyter base (from this process): <code>{base}</code></p>
  <ul>
    <li><strong>Start Jupyter on the host:</strong> <code>cd {nn_root} &amp;&amp; ./start-jupyter.sh</code></li>
    <li><strong>Laptop access:</strong> <code>ssh -L 8890:127.0.0.1:8890 ubuntu@&lt;host&gt;</code> then open the URL printed by Jupyter (includes token).</li>
    <li><strong>Machine-readable:</strong> <a href="/training">GET /training</a> (JSON), <a href="/training/status">GET /training/status</a></li>
  </ul>
  <p>{html.escape(payload["integration_note"])}</p>
  <h2>Full JSON</h2>
  <pre>{safe_json}</pre>
</body>
</html>"""


def training_json_bytes() -> bytes:
    return json.dumps(build_training_payload(), indent=2).encode("utf-8")


def training_status_json_bytes() -> bytes:
    return json.dumps(training_status_payload(), indent=2).encode("utf-8")
