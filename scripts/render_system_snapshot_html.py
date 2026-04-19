#!/usr/bin/env python3
"""
Read ``user_data/system_snapshot.json`` (or ``--in``) and write a **standalone** visual HTML
(default: ``user_data/system_snapshot.html``). Open in any browser (file:// OK).

Layout: **digital space** HUD + animated **dataflow** graph (sources → swarm fuse → snapshot),
plus platform cards and artifact table.

  python3 scripts/render_system_snapshot_html.py
  python3 scripts/render_system_snapshot_html.py --in /tmp/snapshot.json --out /tmp/snap.html
"""
from __future__ import annotations

import argparse
import base64
import json
import sys
from pathlib import Path


def _repo() -> Path:
    return Path(__file__).resolve().parents[1]


# Template uses __PAYLOAD__ only (base64 JSON); no other Python interpolation.
_HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Sygnif · digital snapshot</title>
  <style>
    :root {
      --bg-deep: #080c12;
      --bg-panel: rgba(18, 28, 44, 0.92);
      --grid: rgba(91, 140, 255, 0.08);
      --grid-bright: rgba(91, 140, 255, 0.18);
      --card: #121a28;
      --border: #2a3f5c;
      --text: #e8eef8;
      --muted: #7a8fa8;
      --accent: #5b8cff;
      --cyan: #00d4aa;
      --ok: #3ecf8e;
      --warn: #f5a623;
      --bad: #ff6b6b;
      --flow: #5b8cff;
      --glow: rgba(91, 140, 255, 0.45);
    }
    * { box-sizing: border-box; }
    html { scroll-behavior: smooth; }
    body {
      margin: 0;
      min-height: 100vh;
      font-family: ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, sans-serif;
      color: var(--text);
      line-height: 1.45;
      background: var(--bg-deep);
      background-image:
        linear-gradient(180deg, #0a1018 0%, var(--bg-deep) 45%, #06090e 100%),
        repeating-linear-gradient(90deg, var(--grid) 0 1px, transparent 1px 48px),
        repeating-linear-gradient(0deg, var(--grid) 0 1px, transparent 1px 48px),
        radial-gradient(ellipse 120% 60% at 50% -20%, rgba(91, 140, 255, 0.12), transparent 55%);
      background-attachment: fixed;
    }
    .wrap { max-width: 1100px; margin: 0 auto; padding: 1rem 1rem 2.5rem; position: relative; z-index: 1; }
    .hud-bar {
      display: flex; flex-wrap: wrap; align-items: baseline; justify-content: space-between;
      gap: 0.5rem 1rem; margin-bottom: 0.75rem;
      padding-bottom: 0.75rem; border-bottom: 1px solid var(--border);
    }
    h1 {
      font-size: 1.15rem; font-weight: 600; margin: 0; letter-spacing: 0.04em;
      text-transform: uppercase; color: var(--cyan);
      text-shadow: 0 0 24px var(--glow);
    }
    .sub { color: var(--muted); font-size: 0.8rem; margin: 0; font-family: ui-monospace, monospace; }
    .space-panel {
      background: var(--bg-panel);
      border: 1px solid var(--border);
      border-radius: 14px;
      padding: 1rem 1rem 0.5rem;
      margin-bottom: 1.25rem;
      box-shadow: 0 0 0 1px rgba(0, 212, 170, 0.06), 0 20px 50px rgba(0, 0, 0, 0.45);
      backdrop-filter: blur(8px);
    }
    .space-panel h2 {
      margin: 0 0 0.5rem; font-size: 0.7rem; font-weight: 700;
      letter-spacing: 0.14em; text-transform: uppercase; color: var(--muted);
    }
    .flow-svg { width: 100%; height: auto; display: block; max-height: 380px; }
    .node-disk {
      fill: var(--card); stroke: var(--border); stroke-width: 1.5;
      filter: drop-shadow(0 4px 12px rgba(0,0,0,0.35));
      transition: stroke 0.35s ease, fill 0.35s ease;
    }
    .node-disk.live { stroke: var(--cyan); fill: #0f1f24; }
    .node-disk.warn { stroke: var(--warn); }
    .node-disk.off { stroke: #3a4555; fill: #0d1118; opacity: 0.65; }
    .node-label { fill: var(--text); font-size: 11px; font-weight: 600; font-family: system-ui, sans-serif; }
    .node-sublabel { fill: var(--muted); font-size: 8px; font-family: ui-monospace, monospace; }
    .flow-path {
      fill: none; stroke: var(--border); stroke-width: 1.2; stroke-linecap: round;
      stroke-dasharray: 6 8;
      animation: flowdash 1.2s linear infinite;
    }
    .flow-path.active { stroke: var(--flow); stroke-width: 1.8; opacity: 0.9; }
    .flow-path.dim { opacity: 0.25; animation: none; }
    @keyframes flowdash { to { stroke-dashoffset: -28; } }
    .pulse {
      animation: pulseg 2.4s ease-in-out infinite;
    }
    @keyframes pulseg {
      0%, 100% { filter: drop-shadow(0 0 4px var(--glow)); }
      50% { filter: drop-shadow(0 0 14px var(--cyan)); }
    }
    .grid-cards {
      display: grid; gap: 0.65rem;
      grid-template-columns: repeat(auto-fill, minmax(140px, 1fr));
      margin-bottom: 1rem;
    }
    .card {
      background: var(--card); border: 1px solid var(--border); border-radius: 10px;
      padding: 0.65rem 0.85rem;
    }
    .card k { display: block; font-size: 0.65rem; text-transform: uppercase; letter-spacing: 0.08em; color: var(--muted); margin-bottom: 0.25rem; }
    .card v { font-size: 0.88rem; font-weight: 500; word-break: break-all; }
    table {
      width: 100%; border-collapse: collapse; font-size: 0.78rem;
      background: var(--card); border: 1px solid var(--border); border-radius: 10px; overflow: hidden;
    }
    th, td { padding: 0.55rem 0.65rem; text-align: left; border-bottom: 1px solid var(--border); }
    th { background: #0e1520; color: var(--muted); font-weight: 600; font-size: 0.65rem; text-transform: uppercase; letter-spacing: 0.06em; }
    tr:last-child td { border-bottom: none; }
    .path { font-family: ui-monospace, monospace; font-size: 0.72rem; }
    .badge { display: inline-block; padding: 0.12rem 0.4rem; border-radius: 5px; font-size: 0.65rem; font-weight: 600; }
    .badge-yes { background: rgba(62, 207, 142, 0.15); color: var(--ok); }
    .badge-no { background: rgba(255, 107, 107, 0.12); color: var(--bad); }
    .swarm {
      margin-top: 1rem; padding: 0.9rem 1rem;
      background: linear-gradient(145deg, rgba(26, 39, 64, 0.9) 0%, var(--card) 100%);
      border: 1px solid var(--border); border-radius: 10px;
    }
    .swarm h2 { margin: 0 0 0.6rem; font-size: 0.85rem; color: var(--cyan); letter-spacing: 0.06em; }
    .swarm-row { display: flex; flex-wrap: wrap; gap: 0.65rem 1.1rem; font-size: 0.82rem; }
    .swarm-row span { color: var(--muted); }
    .label-bull { color: var(--ok); font-weight: 600; }
    .label-bear { color: var(--bad); font-weight: 600; }
    .label-mix { color: var(--warn); font-weight: 600; }
    .kp-list { list-style: none; margin: 0; padding: 0; font-size: 0.78rem; }
    .kp-list li {
      padding: 0.4rem 0.55rem; margin-bottom: 0.4rem; border-left: 3px solid var(--border);
      border-radius: 6px; background: rgba(0,0,0,0.22);
    }
    .kp-list li.sev-bull { border-left-color: var(--ok); }
    .kp-list li.sev-bear { border-left-color: var(--bad); }
    .kp-list li.sev-mixed { border-left-color: var(--warn); }
    .kp-list li.sev-warn { border-left-color: var(--warn); }
    .kp-list li.sev-neutral { border-left-color: var(--muted); }
    .kp-node-hint { color: var(--muted); font-size: 0.68rem; margin-left: 0.35rem; font-family: ui-monospace, monospace; }
    footer { margin-top: 1.25rem; font-size: 0.7rem; color: var(--muted); }
    code { font-size: 0.85em; color: var(--accent); }
    .section-title { font-size: 0.72rem; color: var(--muted); margin: 1rem 0 0.4rem; font-weight: 600; letter-spacing: 0.08em; text-transform: uppercase; }
    .flow-mono { font-family: ui-monospace, monospace; font-size: 0.68rem; color: var(--text); background: rgba(0,0,0,0.25); padding: 0.75rem; border-radius: 8px; border: 1px solid var(--border); overflow-x: auto; max-height: 14rem; white-space: pre-wrap; }
  </style>
</head>
<body>
  <div class="wrap">
    <div class="hud-bar">
      <div>
        <h1>Sygnif · digital space</h1>
        <p class="sub" id="sub"></p>
      </div>
      <p class="sub" id="schema-line"></p>
    </div>

    <div class="space-panel">
      <h2>Dataflow · sources → fuse → snapshot</h2>
      <svg class="flow-svg" viewBox="0 0 920 360" xmlns="http://www.w3.org/2000/svg" aria-label="Dataflow">
        <!-- paths: sources to hub -->
        <path id="p-ml" class="flow-path" d="M 110 88 L 110 140 L 430 168" />
        <path id="p-ch" class="flow-path" d="M 270 88 L 270 135 L 450 168" />
        <path id="p-sc" class="flow-path" d="M 430 88 L 430 142 L 458 168" />
        <path id="p-ta" class="flow-path" d="M 590 88 L 590 135 L 470 168" />
        <path id="p-sk" class="flow-path" d="M 750 88 L 750 148 L 485 172" />
        <!-- live Bybit WS tape → fuse -->
        <path id="p-ws" class="flow-path dim" d="M 680 272 L 540 210 L 488 198" />
        <!-- hub to sink -->
        <path id="p-out" class="flow-path" d="M 460 228 L 460 268 L 460 300" />

        <!-- source nodes -->
        <g id="n-ml" transform="translate(110,88)">
          <circle class="node-disk" r="36" cx="0" cy="0" />
          <text class="node-label" text-anchor="middle" y="-6">ML</text>
          <text class="node-sublabel" text-anchor="middle" y="8">predict</text>
        </g>
        <g id="n-ch" transform="translate(270,88)">
          <circle class="node-disk" r="36" cx="0" cy="0" />
          <text class="node-label" text-anchor="middle" y="-6">CH</text>
          <text class="node-sublabel" text-anchor="middle" y="8">channel</text>
        </g>
        <g id="n-sc" transform="translate(430,88)">
          <circle class="node-disk" r="36" cx="0" cy="0" />
          <text class="node-label" text-anchor="middle" y="-6">SC</text>
          <text class="node-sublabel" text-anchor="middle" y="8">sidecar</text>
        </g>
        <g id="n-ta" transform="translate(590,88)">
          <circle class="node-disk" r="36" cx="0" cy="0" />
          <text class="node-label" text-anchor="middle" y="-6">TA</text>
          <text class="node-sublabel" text-anchor="middle" y="8">ta.json</text>
        </g>
        <g id="n-sk" transform="translate(750,88)">
          <circle class="node-disk" r="36" cx="0" cy="0" />
          <text class="node-label" text-anchor="middle" y="-6">SK</text>
          <text class="node-sublabel" text-anchor="middle" y="8">swarm.json</text>
        </g>
        <g id="n-ws" transform="translate(680,272)">
          <circle class="node-disk" r="30" cx="0" cy="0" />
          <text class="node-label" text-anchor="middle" y="-4">WS</text>
          <text class="node-sublabel" text-anchor="middle" y="10">stream</text>
        </g>
        <!-- fuse -->
        <g id="n-fuse" transform="translate(460,200)" class="pulse">
          <rect class="node-disk" x="-52" y="-32" width="104" height="64" rx="12" />
          <text class="node-label" text-anchor="middle" y="-6">SWARM</text>
          <text class="node-sublabel" text-anchor="middle" y="10">fuse</text>
        </g>
        <!-- sink -->
        <g id="n-out" transform="translate(460,318)">
          <rect class="node-disk" x="-70" y="-28" width="140" height="56" rx="10" />
          <text class="node-label" text-anchor="middle" y="-4">VIEW</text>
          <text class="node-sublabel" text-anchor="middle" y="12">system_snapshot</text>
        </g>
      </svg>
    </div>

    <div class="space-panel" id="keypoints-panel" style="display:none;">
      <h2>Swarm annotations · keypoints</h2>
      <p class="sub" style="margin:0 0 0.6rem;">Linked to dataflow nodes (hover SVG). From <code>prediction_agent/swarm_annotations.py</code> + trade monitor.</p>
      <ul class="kp-list" id="swarm-keypoints-list"></ul>
    </div>

    <div class="space-panel" id="trade-dataflow-panel" style="display:none;">
      <h2>Trade monitor · dataflow</h2>
      <p class="sub" style="margin:0 0 0.6rem;">Freqtrade open scope + Bybit WS snapshot + predict iface — <code>trade_dataflow</code> in <code>write_system_snapshot.py</code>. Run <code>scripts/bybit_stream_monitor.py</code> for live tape.</p>
      <pre class="flow-mono" id="trade-dataflow-pre"></pre>
    </div>

    <p class="section-title">Platform</p>
    <div class="grid-cards" id="platform"></div>

    <p class="section-title">Artifacts</p>
    <table>
      <thead><tr><th>Path</th><th>Status</th><th>Size</th><th>Modified (UTC)</th></tr></thead>
      <tbody id="artifacts"></tbody>
    </table>

    <div class="swarm" id="swarm-wrap" style="display:none;">
      <h2>Swarm · embedded metrics</h2>
      <div class="swarm-row" id="swarm"></div>
    </div>

    <footer>Raw JSON: <code>window.__SNAPSHOT__</code> · HTML: <code>python3 scripts/render_system_snapshot_html.py</code> · PNG: <code>.venv/bin/python scripts/system_snapshot_shot.py</code></footer>
  </div>

  <script>
    const raw = atob("__PAYLOAD__");
    const data = JSON.parse(raw);
    window.__SNAPSHOT__ = data;

    document.getElementById("sub").textContent =
      (data.generated_utc || "?") + " · " + (data.repo && data.repo.root ? data.repo.root : "");
    document.getElementById("schema-line").textContent =
      (data.schema || "?") + " v" + (data.version != null ? data.version : "?");

    function exists(sub) {
      var arts = data.artifacts || [];
      for (var i = 0; i < arts.length; i++) {
        var a = arts[i];
        if (a.path && a.path.indexOf(sub) >= 0 && a.exists) return true;
      }
      return false;
    }

    var map = [
      { id: "ml", sub: "btc_prediction_output.json", path: "p-ml" },
      { id: "ch", sub: "training_channel_output.json", path: "p-ch" },
      { id: "sc", sub: "nautilus_strategy_signal.json", path: "p-sc" },
      { id: "ta", sub: "btc_sygnif_ta_snapshot.json", path: "p-ta" },
      { id: "sk", sub: "swarm_knowledge_output.json", path: "p-sk" },
      { id: "ws", sub: "bybit_ws_monitor_state.json", path: "p-ws" }
    ];
    var anyLive = false;
    map.forEach(function (m) {
      var ok = exists(m.sub);
      if (ok) anyLive = true;
      var disk = document.querySelector("#n-" + m.id + " .node-disk");
      if (disk) disk.setAttribute("class", "node-disk " + (ok ? "live" : "off"));
      var p = document.getElementById(m.path);
      if (p) {
        p.setAttribute("class", "flow-path " + (ok ? "active" : "dim"));
      }
    });

    var sw = data.swarm;
    var fuseOk = sw && sw.ok !== false && !(sw.error);
    var fuseEl = document.querySelector("#n-fuse .node-disk");
    if (fuseEl) {
      fuseEl.setAttribute("class", "node-disk " + (fuseOk && anyLive ? "live" : (anyLive ? "warn" : "off")));
    }
    var pOut = document.getElementById("p-out");
    if (pOut) pOut.setAttribute("class", "flow-path " + (fuseOk ? "active" : "dim"));
    var outDisk = document.querySelector("#n-out .node-disk");
    if (outDisk) outDisk.setAttribute("class", "node-disk live");

    var plat = data.platform || {};
    var platEl = document.getElementById("platform");
    [["System", plat.system], ["Release", plat.release], ["Machine", plat.machine], ["Python", plat.python]]
      .filter(function (x) { return x[1]; })
      .forEach(function (pair) {
        var d = document.createElement("div");
        d.className = "card";
        d.innerHTML = "<k>" + pair[0] + "</k><v>" + pair[1] + "</v>";
        platEl.appendChild(d);
      });
    var repo = data.repo || {};
    var rcard = document.createElement("div");
    rcard.className = "card";
    rcard.innerHTML =
      "<k>Git</k><v>" +
      (repo.git_branch || "?") +
      " @ " +
      (repo.git_commit || "?") +
      (repo.git_dirty ? " <span style='color:#f5a623'>(dirty)</span>" : "") +
      "</v>";
    platEl.appendChild(rcard);

    var tb = document.getElementById("artifacts");
    (data.artifacts || []).forEach(function (a) {
      var tr = document.createElement("tr");
      var ok = a.exists;
      tr.innerHTML =
        "<td class='path'>" +
        a.path +
        "</td><td><span class='badge " +
        (ok ? "badge-yes" : "badge-no") +
        "'>" +
        (ok ? "OK" : "MISSING") +
        "</span></td><td>" +
        (ok ? (a.size_bytes != null ? a.size_bytes.toLocaleString() + " B" : "—") : "—") +
        "</td><td style='color:var(--muted)'>" +
        (a.mtime_utc || "—") +
        "</td>";
      tb.appendChild(tr);
    });

    if (sw && typeof sw === "object") {
      document.getElementById("swarm-wrap").style.display = "block";
      var lbl = (sw.swarm_label || "").toUpperCase();
      var cls = "label-mix";
      if (lbl.indexOf("BULL") >= 0) cls = "label-bull";
      else if (lbl.indexOf("BEAR") >= 0) cls = "label-bear";
      var parts = [
        ["Mean", sw.swarm_mean != null ? sw.swarm_mean : "—"],
        ["Label", sw.swarm_label || "—"],
        ["Conflict", sw.swarm_conflict ? "yes" : "no"],
        ["Sources", sw.sources_n != null ? sw.sources_n : "—"],
        ["Missing files", (sw.missing_files && sw.missing_files.length) ? sw.missing_files.join(", ") : "none"],
      ];
      if (sw.error) parts.push(["Error", sw.error]);
      document.getElementById("swarm").innerHTML = parts
        .map(function (p) {
          var v = String(p[1]);
          if (p[0] === "Label") return "<div><span>" + p[0] + ": </span><span class='" + cls + "'>" + v + "</span></div>";
          return "<div><span>" + p[0] + ": </span><strong>" + v + "</strong></div>";
        })
        .join("");
    }

    var td = data.trade_dataflow || {};
    if (td && (td.open_trades || td.ws_live || td.iface_position)) {
      document.getElementById("trade-dataflow-panel").style.display = "block";
      document.getElementById("trade-dataflow-pre").textContent = JSON.stringify(td, null, 2);
    }
    var kps = [].concat((sw && sw.keypoints) || []);
    if (td.extra_keypoints && td.extra_keypoints.length) {
      kps = kps.concat(td.extra_keypoints);
    }
    if (kps.length) {
      document.getElementById("keypoints-panel").style.display = "block";
      var ul = document.getElementById("swarm-keypoints-list");
      kps.forEach(function (kp) {
        var li = document.createElement("li");
        var sev = (kp.severity || "neutral").toLowerCase();
        if (["bull", "bear", "mixed", "warn", "neutral"].indexOf(sev) < 0) sev = "neutral";
        li.className = "sev-" + sev;
        var hint = kp.flow_node
          ? "<span class='kp-node-hint'>[" + kp.flow_node + "]</span>"
          : "";
        li.innerHTML =
          "<strong>" + (kp.label || "") + "</strong> · " + (kp.value || "") + hint;
        ul.appendChild(li);
      });
      var byNode = {};
      kps.forEach(function (kp) {
        if (!kp.flow_node) return;
        if (!byNode[kp.flow_node]) byNode[kp.flow_node] = [];
        byNode[kp.flow_node].push((kp.label || "") + ": " + (kp.value || ""));
      });
      Object.keys(byNode).forEach(function (nid) {
        var el = document.getElementById(nid);
        if (!el) return;
        var t = document.createElementNS("http://www.w3.org/2000/svg", "title");
        t.textContent = byNode[nid].join("\\n");
        el.appendChild(t);
      });
    }
  </script>
</body>
</html>
"""


def _html(data: dict) -> str:
    payload = base64.b64encode(json.dumps(data, separators=(",", ":")).encode("utf-8")).decode("ascii")
    return _HTML_TEMPLATE.replace("__PAYLOAD__", payload)


def main() -> int:
    ap = argparse.ArgumentParser(description="Render system_snapshot.json to standalone HTML")
    ap.add_argument("--in", dest="in_path", type=Path, default=None, help="Input JSON (default: user_data/system_snapshot.json)")
    ap.add_argument("--out", type=Path, default=None, help="Output HTML (default: user_data/system_snapshot.html)")
    args = ap.parse_args()
    root = _repo()
    in_path = args.in_path or (root / "user_data" / "system_snapshot.json")
    out_path = args.out or (root / "user_data" / "system_snapshot.html")
    if not in_path.is_file():
        print(f"render_system_snapshot_html: missing {in_path}", file=sys.stderr)
        return 2
    try:
        data = json.loads(in_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"render_system_snapshot_html: {exc}", file=sys.stderr)
        return 3
    if not isinstance(data, dict):
        print("render_system_snapshot_html: root must be object", file=sys.stderr)
        return 3
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(_html(data), encoding="utf-8")
    print(str(out_path), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
