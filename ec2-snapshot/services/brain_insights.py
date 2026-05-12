#!/usr/bin/env python3
"""sygnif-brain-insights v2 — full read-only dashboard.

Reads brain_state files directly (no GIL contention with brain process):
  meta.json                     step count, neuromodulators, stage, uptime
  regions/*.json                per-neuron Izhikevich v + u + binding_strength
  knowledge.db                  knowledge entries (sqlite WAL-safe RO)
  backups/                      backup history with embedded step counts

Background poller thread reads regions every 2s, detects firings (v>=30mV),
caches per-region stats. Client JS polls /api/state at 1s, animates with
Three.js InstancedMesh — ~3000 neurons rendered as glowing spheres.

3D layout: 11 regions placed deterministically on a sphere, neurons jittered
around region center. Color by region. Brightness by membrane potential
proximity to firing threshold.
"""
from __future__ import annotations

import base64
import datetime as dt
import hashlib
import http.server
import json
import math
import os
import random
import re
import socket as _socket
import socketserver
import sqlite3
import struct
import sys
import threading
import time
from collections import deque
from pathlib import Path

BRAIN_STATE = Path(os.environ.get(
    "SYGNIF_BRAIN_STATE",
    "/home/ubuntu/SYGNIF/third_party/neurolinked/brain_state",
))
PORT = int(os.environ.get("SYGNIF_BRAIN_INSIGHTS_PORT", "8890"))
POLL_INTERVAL_S = 2.0
SAMPLE_NEURONS_PER_REGION = 200  # cap rendered neurons per region for browser perf

# Original NeuroLinked dashboard static-file root (served at /original/*).
# The original dashboard's own HTTP/WS server (port 8889) is GIL-starved by the
# stepping brain; we serve its static UI here so it is at least visible.
ORIGINAL_DASH = Path(os.environ.get(
    "SYGNIF_ORIGINAL_DASH",
    "/opt/sygnif-services/neurolinked_master",
))
_MIME = {
    ".html": "text/html; charset=utf-8",
    ".css":  "text/css; charset=utf-8",
    ".js":   "application/javascript; charset=utf-8",
    ".json": "application/json",
    ".svg":  "image/svg+xml",
    ".png":  "image/png",
    ".ico":  "image/x-icon",
}

# Region color hues (HSL, 0-360) — distinct, perceptually-spaced
REGION_HUES = {
    "sensory_cortex":   200,   # blue
    "motor_cortex":     30,    # orange
    "association":      280,   # purple
    "hippocampus":      120,   # green
    "prefrontal":       0,     # red
    "cerebellum":       180,   # cyan
    "brainstem":        45,    # yellow-orange
    "concept_layer":    320,   # magenta
    "feature_layer":    160,   # teal
    "predictive":       260,   # blue-violet
    "reflex_arc":       60,    # yellow
}

# ---- shared state -------------------------------------------------------
_LOCK = threading.Lock()
_REGION_DATA: dict = {}          # region_name -> {v: [], u: [], binding: [], read_at: ts}
_PREV_REGION_V: dict = {}        # for fire detection
_FIRINGS: dict = {}              # region_name -> deque of fire timestamps (recent)
_3D_POSITIONS: dict = {}         # region_name -> [[x,y,z], ...] deterministic per-neuron
_3D_INIT_DONE = False
_HZ_SAMPLES: deque = deque(maxlen=30)
_HZ_LAST_SAVED_AT = 0.0
_HZ_LAST_BACKUPS_MTIME = 0.0
_BACKUP_DIRNAME_RE = re.compile(r"^(\d{8})_(\d{6})_[A-Z_]+_(\d+)steps_\d+n$")


def _gen_3d_positions_once() -> None:
    """Deterministically place 11 regions on a sphere, neurons jittered around each."""
    global _3D_INIT_DONE
    if _3D_INIT_DONE:
        return
    rng = random.Random(42)  # seed for reproducibility
    region_names = list(REGION_HUES.keys())
    n_regions = len(region_names)
    # Distribute regions as Fibonacci sphere
    region_centers = {}
    phi = math.pi * (3.0 - math.sqrt(5.0))   # golden angle
    for idx, name in enumerate(region_names):
        y = 1.0 - 2.0 * (idx + 0.5) / n_regions
        r = math.sqrt(max(0.0, 1.0 - y * y))
        theta = phi * idx
        cx = math.cos(theta) * r * 8.0
        cy = y * 8.0
        cz = math.sin(theta) * r * 8.0
        region_centers[name] = (cx, cy, cz)

    # Neurons jittered around region center (we'll grow this as we read regions)
    for name, (cx, cy, cz) in region_centers.items():
        # generous default — actual count adjusted in poller
        positions = []
        for _ in range(SAMPLE_NEURONS_PER_REGION):
            r = rng.gauss(0, 1.5)
            ang1 = rng.uniform(0, 2 * math.pi)
            ang2 = rng.uniform(-math.pi/2, math.pi/2)
            dx = r * math.cos(ang2) * math.cos(ang1)
            dy = r * math.sin(ang2)
            dz = r * math.cos(ang2) * math.sin(ang1)
            positions.append([cx + dx, cy + dy, cz + dz])
        _3D_POSITIONS[name] = positions
    _3D_INIT_DONE = True


def _read_region(name: str) -> dict | None:
    """Read one region JSON, return v/u arrays + read timestamp."""
    fp = BRAIN_STATE / "regions" / f"{name}.json"
    try:
        with open(fp) as f:
            d = json.load(f)
        return {
            "v": d.get("v") or [],
            "u": d.get("u") or [],
            "binding": d.get("binding_strength") or [],
            "read_at": time.time(),
            "mtime": fp.stat().st_mtime,
        }
    except Exception:
        return None


def _detect_fires(region: str, prev_v: list, curr_v: list) -> int:
    """Count neurons that crossed firing threshold (v >= 30) between reads."""
    if not prev_v or not curr_v or len(prev_v) != len(curr_v):
        return 0
    fires = 0
    for pv, cv in zip(prev_v, curr_v):
        # Izhikevich: fires when v >= 30. After firing, v resets to c (-65).
        # Detect: prev was below threshold OR detect drop from peak (firing reset)
        if cv >= 30 or (pv > 0 and cv < -50):  # active or just-fired
            fires += 1
    return fires


def _poll_regions_loop() -> None:
    """Background thread — reads all regions every POLL_INTERVAL_S."""
    global _PREV_REGION_V
    while True:
        try:
            new_data = {}
            for name in REGION_HUES.keys():
                d = _read_region(name)
                if d:
                    new_data[name] = d

            if new_data:
                with _LOCK:
                    # detect firings vs previous read
                    for name, data in new_data.items():
                        prev_v = _PREV_REGION_V.get(name) or []
                        n_fires = _detect_fires(name, prev_v, data["v"])
                        if name not in _FIRINGS:
                            _FIRINGS[name] = deque(maxlen=60)
                        _FIRINGS[name].append((data["read_at"], n_fires))
                        _PREV_REGION_V[name] = list(data["v"])
                    _REGION_DATA.clear()
                    _REGION_DATA.update(new_data)
        except Exception as e:
            print(f"[poll] err: {type(e).__name__}: {e}", flush=True)
        time.sleep(POLL_INTERVAL_S)


# ---- read helpers (used by API) ----------------------------------------

def read_live() -> dict | None:
    """Read brain_state/live.json — flushed every ~1s by the brain itself.
    Returns None if missing or older than 10s (brain restarting / patch lost).
    """
    fp = BRAIN_STATE / "live.json"
    try:
        st = fp.stat()
        if time.time() - st.st_mtime > 10.0:
            return None
        with open(fp) as f:
            d = json.load(f)
        d["_age_s"] = time.time() - st.st_mtime
        return d
    except Exception:
        return None


def read_meta() -> dict:
    try:
        return json.load(open(BRAIN_STATE / "meta.json"))
    except Exception as e:
        return {"error": str(e)}


_MARKET_RE = re.compile(
    r"MARKET\s+(?P<sym>\S+)\s+mid=(?P<mid>[0-9.]+)\s+"
    r"bid=(?P<bid>[0-9.]+)\s+ask=(?P<ask>[0-9.]+)\s+"
    r"spread=(?P<spr>[0-9.]+)bps"
)


def _parse_market(text: str) -> dict | None:
    if not text:
        return None
    m = _MARKET_RE.search(text)
    if not m:
        return None
    g = m.groupdict()
    try:
        return {
            "sym":    g["sym"],
            "mid":    float(g["mid"]),
            "bid":    float(g["bid"]),
            "ask":    float(g["ask"]),
            "spread_bps": float(g["spr"]),
        }
    except ValueError:
        return None


def read_knowledge_stats() -> dict:
    try:
        con = sqlite3.connect(f"file:{BRAIN_STATE}/knowledge.db?mode=ro", uri=True, timeout=5)
        cur = con.execute("SELECT COUNT(*), MAX(timestamp), AVG(strength), SUM(access_count) FROM knowledge")
        n, max_ts, avg_s, ac = cur.fetchone()
        cur = con.execute("SELECT source, COUNT(*) FROM knowledge GROUP BY source ORDER BY 2 DESC LIMIT 6")
        sources = dict(cur.fetchall())
        # growth windows — true real-time learning rate
        now_epoch = time.time()
        cur = con.execute("SELECT COUNT(*) FROM knowledge WHERE timestamp > ?", (now_epoch - 300,))
        recent_5m = cur.fetchone()[0]
        cur = con.execute("SELECT COUNT(*) FROM knowledge WHERE timestamp > ?", (now_epoch - 60,))
        recent_1m = cur.fetchone()[0]
        cur = con.execute("SELECT COUNT(*) FROM knowledge WHERE timestamp > ?", (now_epoch - 10,))
        recent_10s = cur.fetchone()[0]
        # latest market tape snapshot
        cur = con.execute(
            "SELECT text, timestamp FROM knowledge "
            "WHERE text LIKE 'MARKET %' ORDER BY timestamp DESC LIMIT 1"
        )
        row = cur.fetchone()
        market = None
        market_age_s = None
        if row:
            market = _parse_market(row[0])
            try:
                market_age_s = max(0.0, now_epoch - float(row[1]))
            except (TypeError, ValueError):
                market_age_s = None
        # latest swarm bridge entry
        cur = con.execute(
            "SELECT substr(text,1,140), timestamp FROM knowledge "
            "WHERE source='sygnif_swarm' ORDER BY timestamp DESC LIMIT 1"
        )
        srow = cur.fetchone()
        swarm_text = srow[0] if srow else None
        swarm_age_s = (now_epoch - float(srow[1])) if srow and srow[1] else None
        con.close()
        return {
            "total_entries": n or 0,
            "newest_ts": max_ts,
            "avg_strength": float(avg_s or 0),
            "total_access": ac or 0,
            "by_source": sources,
            "added_last_5m": recent_5m,
            "added_last_1m": recent_1m,
            "added_last_10s": recent_10s,
            "learn_hz": (recent_10s / 10.0),
            "market": market,
            "market_age_s": market_age_s,
            "swarm_text": swarm_text,
            "swarm_age_s": swarm_age_s,
        }
    except Exception as e:
        return {"error": str(e)}


def read_recent_knowledge(limit: int = 20) -> list[dict]:
    try:
        con = sqlite3.connect(f"file:{BRAIN_STATE}/knowledge.db?mode=ro", uri=True, timeout=5)
        con.row_factory = sqlite3.Row
        cur = con.execute(
            "SELECT id, substr(text,1,260) as text, source, timestamp, strength, access_count "
            "FROM knowledge ORDER BY timestamp DESC LIMIT ?", (limit,))
        return [dict(r) for r in cur.fetchall()]
    except Exception as e:
        return [{"error": str(e)}]


def read_backups() -> dict:
    bdir = BRAIN_STATE / "backups"
    if not bdir.exists():
        return {"count": 0, "list": []}
    items = []
    try:
        for entry in sorted(bdir.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
            m = _BACKUP_DIRNAME_RE.match(entry.name)
            steps = int(m.group(3)) if m else None
            items.append({
                "name": entry.name,
                "mtime": entry.stat().st_mtime,
                "steps": steps,
            })
    except Exception:
        pass
    return {"count": len(items), "list": items[:8]}


def _ingest_hz(meta: dict) -> None:
    global _HZ_LAST_SAVED_AT, _HZ_LAST_BACKUPS_MTIME
    saved_at = float(meta.get("saved_at") or 0)
    steps = int(meta.get("step_count") or 0)
    with _LOCK:
        if saved_at > _HZ_LAST_SAVED_AT and steps > 0:
            _HZ_SAMPLES.append((saved_at, steps))
            _HZ_LAST_SAVED_AT = saved_at
        # backup-name samples for finer granularity
        try:
            bdir = BRAIN_STATE / "backups"
            if bdir.exists() and bdir.stat().st_mtime > _HZ_LAST_BACKUPS_MTIME:
                _HZ_LAST_BACKUPS_MTIME = bdir.stat().st_mtime
                for entry in bdir.iterdir():
                    if not entry.is_dir(): continue
                    m = _BACKUP_DIRNAME_RE.match(entry.name)
                    if not m: continue
                    date_s, time_s, step_s = m.groups()
                    try:
                        ts = dt.datetime.strptime(date_s + time_s, "%Y%m%d%H%M%S").replace(
                            tzinfo=dt.timezone.utc).timestamp()
                        smp = (ts, int(step_s))
                        if smp not in _HZ_SAMPLES:
                            _HZ_SAMPLES.append(smp)
                    except ValueError: pass
                ss = sorted(set(_HZ_SAMPLES))
                _HZ_SAMPLES.clear()
                _HZ_SAMPLES.extend(ss[-30:])
        except Exception: pass


def _compute_hz() -> dict:
    with _LOCK:
        s = sorted(_HZ_SAMPLES)
    if len(s) < 2:
        return {"hz_5m": None, "hz_recent": None, "hz_lifetime": None, "samples": len(s)}
    now = s[-1][0]
    def hz(window):
        if len(window) < 2: return None
        dt_s = window[-1][0] - window[0][0]
        ds = window[-1][1] - window[0][1]
        # need at least 30s span to avoid divide-by-tiny-dt blowups
        if dt_s < 30: return None
        return ds / dt_s
    return {
        "hz_5m":     hz([x for x in s if x[0] >= now - 300]),
        "hz_recent": hz([x for x in s if x[0] >= now - 600]),
        "hz_lifetime": hz(s),
        "samples": len(s),
        "newest_steps": s[-1][1],
        "window_min": (s[-1][0] - s[0][0]) / 60,
    }


def gather_region_summary() -> dict:
    """Per-region aggregate stats from cached poll data."""
    out = {}
    with _LOCK:
        data = dict(_REGION_DATA)
        firings = {n: list(d) for n, d in _FIRINGS.items()}
    for name, d in data.items():
        v = d.get("v") or []
        if not v:
            continue
        n_total = len(v)
        n_active = sum(1 for x in v if x > -50)
        n_firing = sum(1 for x in v if x >= 30)
        avg_v = sum(v) / n_total if n_total else 0
        fire_history = firings.get(name, [])
        recent_fires = sum(f for _, f in fire_history[-10:])  # last ~20s
        out[name] = {
            "neurons": n_total,
            "active": n_active,
            "firing_now": n_firing,
            "avg_v": round(avg_v, 2),
            "recent_fires_20s": recent_fires,
            "hue": REGION_HUES.get(name, 0),
        }
    return out


def gather_visualization_data() -> dict:
    """Sample of neurons + their states for 3D rendering."""
    out_neurons = []
    with _LOCK:
        data = dict(_REGION_DATA)
    _gen_3d_positions_once()
    for region_name, d in data.items():
        v = d.get("v") or []
        if not v:
            continue
        positions = _3D_POSITIONS.get(region_name) or []
        # sample SAMPLE_NEURONS_PER_REGION at most
        n = min(len(v), len(positions), SAMPLE_NEURONS_PER_REGION)
        # uniform stride sample
        if len(v) > n:
            step = len(v) // n
            sampled_v = [v[i*step] for i in range(n)]
        else:
            sampled_v = v[:n]
        for i in range(n):
            vi = sampled_v[i]
            # excitation = how close to firing threshold (-65 rest, 30 fire)
            # normalize to [0, 1]
            excite = max(0.0, min(1.0, (vi + 65) / 95))
            out_neurons.append({
                "p": positions[i],         # [x, y, z]
                "r": region_name,          # region key
                "h": REGION_HUES.get(region_name, 0),
                "e": round(excite, 3),     # excitation 0..1 (brightness)
                "f": 1 if vi >= 30 else 0, # currently firing
            })
    return {"neurons": out_neurons, "count": len(out_neurons)}


def gather_state() -> dict:
    meta = read_meta()
    _ingest_hz(meta)
    return {
        "ts_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "meta": meta,
        "knowledge": read_knowledge_stats(),
        "backups": read_backups(),
        "regions": gather_region_summary(),
        "hz": _compute_hz(),
        "viz": gather_visualization_data(),
        "brain_state_path": str(BRAIN_STATE),
    }


# ---- WebSocket support for the GitHub-master dashboard ------------------
# The master dashboard expects:
#   {"type":"init","positions": {region: {positions: [[x,y,z],...], center: [...], count: N}}}
#   {"type":"state","data": {total_neurons, total_synapses, step, steps_per_second,
#                            development_stage, neuromodulators{4}, regions{name:{firing_rate, neuron_count}}}}
# Master scales: brain3d.js does (pos[i] - center) * 1.2 + layout — so we send
# positions whose offset from center is small (~±0.3) to fit the master layout.

_WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
_MASTER_INIT_POSITIONS_CACHE: dict | None = None


def _ws_accept_key(client_key: str) -> str:
    sha = hashlib.sha1((client_key + _WS_GUID).encode("ascii")).digest()
    return base64.b64encode(sha).decode("ascii")


def _ws_text_frame(payload: bytes) -> bytes:
    n = len(payload)
    if n <= 125:
        return bytes([0x81, n]) + payload
    if n <= 65535:
        return bytes([0x81, 126]) + struct.pack(">H", n) + payload
    return bytes([0x81, 127]) + struct.pack(">Q", n) + payload


def build_master_init_positions() -> dict:
    """Per-region neuron positions in master's coordinate scale (small deltas)."""
    global _MASTER_INIT_POSITIONS_CACHE
    if _MASTER_INIT_POSITIONS_CACHE is not None:
        return _MASTER_INIT_POSITIONS_CACHE
    rng = random.Random(7)
    out = {}
    for name in REGION_HUES:
        d = _read_region(name)
        n_target = len(d.get("v", [])) if d else 100
        if n_target <= 0:
            n_target = 100
        # Larger regions get a wider cloud so density stays visually consistent.
        # association (~1092) -> ~0.27 spread; small regions (100) -> ~0.18.
        spread = 0.13 + 0.02 * math.log(max(2, n_target))
        pts = []
        for _ in range(n_target):
            # gaussian cluster around (0,0,0) - master places it at REGION_LAYOUT.x/y/z
            x = rng.gauss(0, spread)
            y = rng.gauss(0, spread)
            z = rng.gauss(0, spread)
            pts.append([round(x, 4), round(y, 4), round(z, 4)])
        out[name] = {"positions": pts, "center": [0.0, 0.0, 0.0], "count": n_target}
    _MASTER_INIT_POSITIONS_CACHE = out
    return out


def compute_real_recent_hz() -> float | None:
    """Stepping rate from the two most recent backup directories.
    Each persistence save creates one backup with the step count baked into
    the directory name. The delta between the last two = honest current rate.
    Returns None if fewer than 2 backups.
    """
    bdir = BRAIN_STATE / "backups"
    if not bdir.exists():
        return None
    rows = []
    try:
        for entry in bdir.iterdir():
            if not entry.is_dir():
                continue
            m = _BACKUP_DIRNAME_RE.match(entry.name)
            if not m:
                continue
            date_s, time_s, step_s = m.groups()
            try:
                ts = dt.datetime.strptime(date_s + time_s, "%Y%m%d%H%M%S").replace(
                    tzinfo=dt.timezone.utc).timestamp()
            except ValueError:
                continue
            rows.append((ts, int(step_s)))
    except Exception:
        return None
    if len(rows) < 2:
        return None
    rows.sort()
    t1, s1 = rows[-2]
    t2, s2 = rows[-1]
    dt_s = t2 - t1
    if dt_s <= 0:
        return None
    return (s2 - s1) / dt_s


def build_master_state() -> dict:
    """State message in the shape master/dashboard.js expects.

    Repurposes the master "Claude Bridge" panel to surface the SYGNIF swarm
    bridge instead — interactions = total swarm-source knowledge entries,
    screen.active = swarm channel fresh, screen.motion = live ingest velocity.

    Honest data:
      step             = meta.step_count (frozen between persistence saves)
      steps_per_second = compute_real_recent_hz() — actual delta between the
                         last two saves (e.g. ~4.6 Hz). NOT a lifetime ratio.
    """
    meta = read_meta()
    live = read_live()  # fresh (~1s) brain stats from live.json, or None
    _ingest_hz(meta)
    hz = _compute_hz()
    knowledge = read_knowledge_stats()
    regions_summary = gather_region_summary()
    # master shape — { region: { firing_rate: 0..1, neuron_count: N } }
    regions_out = {}
    for name, r in regions_summary.items():
        nc = max(1, r.get("neurons", 0))
        # active is # neurons above resting potential — scale to 0..1
        firing = min(1.0, (r.get("active", 0) / nc) + 0.05 * (r.get("recent_fires_20s", 0) > 0))
        regions_out[name] = {
            "firing_rate": round(firing, 4),
            "neuron_count": nc,
        }
    # Prefer live.json (refreshed every ~1s by the brain itself). Fall back to
    # meta.json (every ~7 min) + backup-delta hz if live.json missing/stale.
    if live is not None:
        step_now = int(live.get("step_count") or 0)
        sps = float(live.get("steps_per_second") or 0.0)
        nm = live.get("neuromodulators") or meta.get("neuromodulators") or {}
        dev_stage = live.get("development_stage") or meta.get("development_stage", "?")
        total_synapses_now = int(live.get("total_synapses") or 0)
        live_age = float(live.get("_age_s") or 0.0)
    else:
        step_now = int(meta.get("step_count") or 0)
        sps = compute_real_recent_hz() or hz.get("hz_recent") or 0.0
        nm = meta.get("neuromodulators") or {}
        dev_stage = meta.get("development_stage", "?")
        total_synapses_now = 0
        live_age = None
    # Honest neuron count = sum across the 11 region JSON files (3061), not the
    # configured meta.total_neurons (3000). The 61-neuron delta accumulates as
    # the brain reshapes during development — show what's actually loaded.
    total_neurons_now = sum(r.get("neurons", 0) for r in regions_summary.values()) or int(
        (live or {}).get("total_neurons") or meta.get("total_neurons", 0)
    )
    saved_at = float(meta.get("saved_at") or 0.0)
    # synapse count from synapses/*.npz file count (each = inter-region group)
    try:
        synapse_dir = BRAIN_STATE / "synapses"
        n_synapses = sum(1 for _ in synapse_dir.glob("*.npz")) if synapse_dir.exists() else 0
    except Exception:
        n_synapses = 0
    return {
        "total_neurons": total_neurons_now,
        "total_synapses": total_synapses_now or n_synapses,
        "step": step_now,
        "steps_per_second": float(sps or 0.0),
        "saved_age_s": (time.time() - saved_at) if saved_at > 0 else None,
        "live_age_s": live_age,
        "live_source": "live.json" if live is not None else "meta.json",
        "development_stage": dev_stage,
        "neuromodulators": {
            "dopamine":       float(nm.get("dopamine", 0)),
            "acetylcholine":  float(nm.get("acetylcholine", 0)),
            "norepinephrine": float(nm.get("norepinephrine", 0)),
            "serotonin":      float(nm.get("serotonin", 0)),
        },
        "regions": regions_out,
        "safety": {"emergency_stop": False, "block_rate": 0.0, "passed": step_now},
        # SYGNIF swarm bridge mapped onto the original Claude Bridge panel:
        #   interactions = total knowledge entries from sygnif_swarm + user (BTC tape)
        #                  + sygnif_swarm grouped — gives the full "what flowed in" count
        #   screen.active = ingest pipeline is fresh (last entry < 60s)
        #   screen.motion = live learn velocity (entries/sec, clamped to [0..1])
        "claude": {
            "interactions": int(_swarm_interaction_count(knowledge)),
            "swarm_label":  _swarm_label_from(knowledge),
            "swarm_age_s":  knowledge.get("swarm_age_s"),
            "market_age_s": knowledge.get("market_age_s"),
        },
        "screen_observer": {
            "active": bool((knowledge.get("market_age_s") or 1e9) < 60),
            "motion": float(min(1.0, (knowledge.get("learn_hz") or 0.0))),
        },
    }


def _swarm_interaction_count(knowledge: dict) -> int:
    """Total knowledge entries from feeds that drive the brain via the swarm bridge.
    Includes the BTC market tape (source='user') and swarm-bridge labels (source='sygnif_swarm').
    """
    by_src = knowledge.get("by_source") or {}
    return int((by_src.get("sygnif_swarm") or 0) + (by_src.get("user") or 0))


def _swarm_label_from(knowledge: dict) -> str | None:
    """Extract the most recent SWARM_* label from the swarm-bridge feed text."""
    txt = knowledge.get("swarm_text") or ""
    m = re.search(r"SWARM_(BULL|BEAR|MIXED|NEUTRAL)", txt)
    return m.group(0) if m else None


# ---- HTML page (Three.js inline) ---------------------------------------

HTML = r"""<!doctype html>
<html><head>
<meta charset="utf-8">
<title>SYGNIF NeuroLinked — read-only insights</title>
<script src="https://unpkg.com/three@0.158.0/build/three.min.js"></script>
<style>
* { box-sizing: border-box; }
body { background:#0a0a0a; color:#eee; margin:0;
       font: 13px ui-monospace, 'SF Mono', monospace; overflow:hidden; }
#root { display:grid; grid-template-columns: 1fr 360px; height:100vh; }
#canvas-wrap { position:relative; background: radial-gradient(circle at 50% 50%, #111 0%, #000 100%); }
#canvas-wrap canvas { display:block; }
#overlay { position:absolute; top:10px; left:10px; pointer-events:none; padding:8px 12px;
           background:rgba(20,20,20,0.7); border:1px solid #333; border-radius:6px; }
#overlay h1 { margin:0 0 4px 0; font-size:14px; color:#ffaa00; }
#overlay small { color:#888; font-size:11px; }
#sidebar { background:#0f0f0f; border-left:1px solid #222; overflow-y:auto; padding:14px; }
.box { background:#1a1a1a; border:1px solid #2a2a2a; border-radius:6px;
       padding:10px 12px; margin-bottom:12px; }
.box h3 { margin:0 0 8px 0; font-size:12px; color:#ffaa00; text-transform:uppercase;
          letter-spacing:0.05em; }
.kv { display:flex; justify-content:space-between; padding:3px 0;
      border-bottom:1px solid #222; font-size:12px; }
.kv:last-child { border:0; }
.k { color:#888; }
.v { color:#fff; font-weight:600; }
.stage { color:#ffaa00; font-weight:bold; }
.nm { display:grid; grid-template-columns: 90px 1fr 50px; gap:6px; align-items:center;
      padding:3px 0; font-size:11px; }
.nm-bar { background:#222; height:14px; border-radius:7px; overflow:hidden; }
.nm-fill { height:100%; transition: width 0.5s; }
.nm-val { text-align:right; color:#aaa; }
.region-row { display:grid; grid-template-columns: 16px 1fr 50px 50px; gap:6px;
              align-items:center; padding:3px 0; font-size:11px; border-bottom:1px solid #222; }
.region-row:last-child { border:0; }
.region-dot { width:12px; height:12px; border-radius:50%; }
.region-name { color:#ddd; }
.region-stat { text-align:right; color:#aaa; font-size:10px; }
.region-fire { color:#ff6633; font-weight:bold; }
.entry { padding:4px 0; border-bottom:1px solid #222; font-size:11px; }
.entry .src { color:#66aaff; font-size:9px; }
.entry .text { color:#ccc; }
#fps { color:#33ff66; font-size:10px; }
.dim { color:#666; }
.section { margin-top:6px; }
.knowledge-list { max-height:160px; overflow-y:auto; }
#live-banner { position:absolute; top:10px; right:10px; padding:10px 16px;
  background:rgba(20,20,20,0.85); border:1px solid #2a2a2a; border-radius:6px;
  pointer-events:none; min-width:280px; }
#live-banner .lb-row { display:flex; justify-content:space-between; gap:14px; align-items:baseline; }
#live-banner .lb-px { font:bold 22px ui-monospace,monospace; color:#33ff66; }
#live-banner .lb-px.stale { color:#ff6633; }
#live-banner .lb-sym { color:#888; font-size:10px; }
#live-banner .lb-stat { font-size:10px; color:#888; }
#live-banner .lb-pulse { font-size:10px; color:#33ff66; }
@keyframes pulse { 0%{opacity:1} 50%{opacity:0.3} 100%{opacity:1} }
.pulsing { animation: pulse 0.6s ease-out; }
</style>
</head><body>
<div id="root">
  <div id="canvas-wrap">
    <div id="overlay">
      <h1>🧠 NeuroLinked — read-only mirror</h1>
      <small>Three.js / no GIL contention / poll 1s · <span id="fps">— FPS</span></small>
      <div id="diag" style="color:#888;font-size:9px;margin-top:4px">init...</div>
    </div>
    <div id="live-banner">
      <div class="lb-row">
        <span class="lb-sym" id="lb-sym">—</span>
        <span class="lb-pulse" id="lb-pulse">●</span>
      </div>
      <div class="lb-row">
        <span class="lb-px" id="lb-px">—</span>
        <span class="lb-stat" id="lb-spread">—</span>
      </div>
      <div class="lb-row">
        <span class="lb-stat" id="lb-bidask">bid — ask —</span>
        <span class="lb-stat" id="lb-age">—</span>
      </div>
      <div class="lb-row" style="margin-top:6px;border-top:1px solid #222;padding-top:6px">
        <span class="lb-stat">learn rate</span>
        <span class="lb-stat" id="lb-learn">— /s · — /min · — /5min</span>
      </div>
      <div class="lb-row">
        <span class="lb-stat">swarm feed</span>
        <span class="lb-stat" id="lb-swarm">—</span>
      </div>
    </div>
    <div id="three-container" style="width:100%;height:100%"></div>
  </div>
  <div id="sidebar">

    <div class="box" id="overview">
      <h3>Overview</h3>
      <div class="kv"><span class="k">Step</span><span class="v" id="step">—</span></div>
      <div class="kv"><span class="k">Stage</span><span class="v stage" id="stage">—</span></div>
      <div class="kv"><span class="k">Neurons</span><span class="v" id="neurons">—</span></div>
      <div class="kv"><span class="k">Uptime</span><span class="v" id="uptime">—</span></div>
      <div class="kv"><span class="k">Last save</span><span class="v" id="last-save">—</span></div>
    </div>

    <div class="box">
      <h3>⚡ Step Hz</h3>
      <div class="kv"><span class="k">5 min</span><span class="v" style="color:#33ff66" id="hz5m">—</span></div>
      <div class="kv"><span class="k">10 min</span><span class="v" id="hz10m">—</span></div>
      <div class="kv"><span class="k">Lifetime</span><span class="v" id="hzlife">—</span></div>
      <div class="kv"><span class="k">Window</span><span class="v dim" id="hzwin">—</span></div>
    </div>

    <div class="box">
      <h3>Neuromodulators</h3>
      <div id="nm-block"></div>
    </div>

    <div class="box">
      <h3>11 Regions — live</h3>
      <div id="regions-list"></div>
    </div>

    <div class="box">
      <h3>Knowledge Store</h3>
      <div class="kv"><span class="k">Entries</span><span class="v" id="kn-total">—</span></div>
      <div class="kv"><span class="k">Last 5 min</span><span class="v" id="kn-5m">—</span></div>
      <div class="kv"><span class="k">Avg strength</span><span class="v" id="kn-avg">—</span></div>
      <div class="kv"><span class="k">Total access</span><span class="v" id="kn-access">—</span></div>
      <div class="kv"><span class="k">Top sources</span><span class="v" id="kn-src" style="font-size:10px">—</span></div>
    </div>

    <div class="box">
      <h3>Backups</h3>
      <div class="kv"><span class="k">Total</span><span class="v" id="bk-count">—</span></div>
      <div class="kv"><span class="k">Latest</span><span class="v dim" style="font-size:10px" id="bk-latest">—</span></div>
    </div>

    <div class="box">
      <h3>Recent Knowledge</h3>
      <div class="knowledge-list" id="recent"></div>
    </div>

    <small style="color:#666">Endpoints: <a style="color:#66aaff" href="/api/state">/api/state</a> ·
      <a style="color:#66aaff" href="/api/recent">/api/recent</a></small>
  </div>
</div>

<script>
let scene, camera, renderer, instMesh, dummy;
let neuronCount = 0;
let neuronStates = [];  // {hue, excite, fire}
let lastFrameTime = performance.now(); let frameCount = 0;

function initThree() {
  const container = document.getElementById('three-container');
  const w = container.clientWidth, h = container.clientHeight;
  scene = new THREE.Scene();
  camera = new THREE.PerspectiveCamera(60, w/h, 0.1, 100);
  camera.position.set(0, 0, 22);
  renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
  renderer.setSize(w, h);
  renderer.setClearColor(0x000000, 0);
  container.appendChild(renderer.domElement);
  // ambient + key light
  scene.add(new THREE.AmbientLight(0x404040, 1.2));
  const keylight = new THREE.PointLight(0xffffff, 1, 50);
  keylight.position.set(10, 10, 10);
  scene.add(keylight);
  window.addEventListener('resize', () => {
    const w = container.clientWidth, h = container.clientHeight;
    camera.aspect = w/h; camera.updateProjectionMatrix();
    renderer.setSize(w, h);
  });
  dummy = new THREE.Object3D();

  // diagnostic test markers — known-bright colored spheres at fixed positions
  // to verify the rendering pipeline works independent of InstancedMesh.
  const testGeo = new THREE.SphereGeometry(0.6, 16, 12);
  const testColors = [0xff3344, 0x33ff66, 0x3399ff, 0xffcc33];
  testColors.forEach((col, i) => {
    const m = new THREE.MeshBasicMaterial({ color: col, transparent: true, opacity: 0.8,
      blending: THREE.AdditiveBlending, depthWrite: false });
    const s = new THREE.Mesh(testGeo, m);
    s.position.set(-9 + i * 6, 8.5, 0);
    scene.add(s);
  });
}

function rebuildInstancedNeurons(neurons) {
  if (instMesh) { scene.remove(instMesh); instMesh.dispose && instMesh.dispose(); }
  const geom = new THREE.SphereGeometry(0.18, 12, 10);
  // MeshBasicMaterial — no lighting needed; InstancedMesh handles instanceColor
  // automatically. DO NOT set vertexColors:true — that would multiply with
  // (zero) geometry vertex colors and zero out everything.
  const mat = new THREE.MeshBasicMaterial({
    color: 0xffffff,
    transparent: true,
    opacity: 0.95,
    blending: THREE.AdditiveBlending,
    depthWrite: false,
  });
  instMesh = new THREE.InstancedMesh(geom, mat, neurons.length);
  instMesh.instanceMatrix.setUsage(THREE.DynamicDrawUsage);
  scene.add(instMesh);
  neuronCount = neurons.length;
  neuronStates = neurons.map(n => ({ hue: n.h, excite: n.e, fire: n.f, p: n.p }));
  const c = new THREE.Color();
  for (let i = 0; i < neurons.length; i++) {
    const n = neurons[i];
    dummy.position.set(n.p[0], n.p[1], n.p[2]);
    dummy.scale.setScalar(1.0);
    dummy.updateMatrix();
    instMesh.setMatrixAt(i, dummy.matrix);
    c.setHSL(n.h / 360, 1.0, 0.6);
    instMesh.setColorAt(i, c);
  }
  instMesh.instanceMatrix.needsUpdate = true;
  if (instMesh.instanceColor) instMesh.instanceColor.needsUpdate = true;
  // diagnostic readout
  const dbg = document.getElementById('diag');
  if (dbg) {
    const ic = instMesh.instanceColor;
    const has = ic ? 'yes' : 'NULL';
    const sample = ic ? Array.from(ic.array.slice(0, 9)).map(v => v.toFixed(2)).join(',') : '—';
    dbg.textContent = `instColor=${has} n=${neuronCount} sample=${sample} threejs=${THREE.REVISION}`;
  }
}

function updateNeuronStates(neurons) {
  if (!instMesh || neurons.length !== neuronCount) {
    rebuildInstancedNeurons(neurons);
    return;
  }
  const c = new THREE.Color();
  for (let i = 0; i < neurons.length; i++) {
    const n = neurons[i];
    neuronStates[i] = { hue: n.h, excite: n.e, fire: n.f, p: n.p };
    // additive blending → high baseline lightness, excite + fire boost on top
    const lightness = 0.50 + 0.30 * n.e;  // 0.50..0.80
    c.setHSL(n.h / 360, 1.0, lightness);
    if (n.f) c.setHSL(0.13, 1.0, 0.95);
    instMesh.setColorAt(i, c);
  }
  if (instMesh.instanceColor) instMesh.instanceColor.needsUpdate = true;
}

function animate() {
  requestAnimationFrame(animate);
  if (instMesh) {
    // gentle rotation
    instMesh.rotation.y += 0.0015;
    instMesh.rotation.x = Math.sin(performance.now() * 0.0003) * 0.15;
    // pulse scale per-neuron — combines static excitation with live learn-rate breathing
    const t = performance.now() * 0.001;
    const learnHz = window.__learnHz || 0;
    const breathe = 0.5 + 0.5 * Math.sin(t * (1 + learnHz * 4));
    const learnGain = Math.min(1, learnHz / 0.5);  // saturates at 0.5 entries/sec
    for (let i = 0; i < neuronCount; i++) {
      const s = neuronStates[i];
      if (!s) continue;
      // per-neuron phase offset so they don't all pulse in sync
      const phase = (i * 0.137) % 1;
      const localBreathe = 0.5 + 0.5 * Math.sin(t * (1 + learnHz * 4) + phase * 6.28);
      const scl = 0.7 + s.excite * 0.6 + (s.fire ? 0.8 : 0) + learnGain * 0.4 * localBreathe;
      const p = s.p;
      dummy.position.set(p[0], p[1], p[2]);
      dummy.scale.setScalar(scl);
      dummy.updateMatrix();
      instMesh.setMatrixAt(i, dummy.matrix);
    }
    instMesh.instanceMatrix.needsUpdate = true;
  }
  renderer.render(scene, camera);
  // FPS
  const now = performance.now();
  frameCount++;
  if (now - lastFrameTime > 1000) {
    document.getElementById('fps').textContent =
      Math.round(frameCount * 1000 / (now - lastFrameTime)) + ' FPS';
    lastFrameTime = now; frameCount = 0;
  }
}

function fmtNumber(n) { if (n == null) return '—'; if (n >= 1e6) return (n/1e6).toFixed(2)+'M';
  if (n >= 1e3) return (n/1e3).toFixed(2)+'k'; return Math.round(n).toString(); }
function fmtHz(v) { if (v == null) return '—'; if (v >= 1000) return (v/1000).toFixed(2)+'k Hz'; return v.toFixed(1)+' Hz'; }

async function poll() {
  try {
    const r = await fetch('/api/state', { cache: 'no-store' });
    const s = await r.json();
    // overview
    const meta = s.meta || {};
    document.getElementById('step').textContent = (meta.step_count || 0).toLocaleString();
    document.getElementById('stage').textContent = meta.development_stage || '?';
    document.getElementById('neurons').textContent = (meta.total_neurons || 0).toLocaleString();
    document.getElementById('uptime').textContent = ((meta.uptime || 0) / 3600).toFixed(1) + ' h';
    if (meta.saved_at) {
      document.getElementById('last-save').textContent =
        new Date(meta.saved_at * 1000).toISOString().slice(11, 19) + 'Z';
    }
    // hz
    const h = s.hz || {};
    document.getElementById('hz5m').textContent = fmtHz(h.hz_5m);
    document.getElementById('hz10m').textContent = fmtHz(h.hz_recent);
    document.getElementById('hzlife').textContent = fmtHz(h.hz_lifetime);
    document.getElementById('hzwin').textContent = (h.samples||0) + ' smp / ' + ((h.window_min||0).toFixed(1)) + ' min';
    // neuromodulators
    const nm = meta.neuromodulators || {};
    const colors = { dopamine:'#ffcc33', acetylcholine:'#33ccff', norepinephrine:'#ff6633', serotonin:'#33ff66' };
    document.getElementById('nm-block').innerHTML = Object.entries(nm).map(([k,v]) =>
      `<div class="nm"><span>${k}</span>
       <div class="nm-bar"><div class="nm-fill" style="width:${(v*100).toFixed(0)}%;background:${colors[k]||'#999'};"></div></div>
       <span class="nm-val">${v.toFixed(3)}</span></div>`
    ).join('');
    // regions
    const regions = s.regions || {};
    document.getElementById('regions-list').innerHTML = Object.entries(regions).map(([name, r]) =>
      `<div class="region-row">
         <span class="region-dot" style="background:hsl(${r.hue},75%,55%)"></span>
         <span class="region-name">${name}</span>
         <span class="region-stat">${r.neurons}n</span>
         <span class="region-stat ${r.firing_now>0?'region-fire':''}">${r.firing_now>0 ? '🔥'+r.firing_now : r.active+'a'}</span>
       </div>`
    ).join('');
    // knowledge
    const k = s.knowledge || {};
    document.getElementById('kn-total').textContent = (k.total_entries||0).toLocaleString();
    document.getElementById('kn-5m').textContent = k.added_last_5m || 0;
    document.getElementById('kn-avg').textContent = (k.avg_strength||0).toFixed(3);
    document.getElementById('kn-access').textContent = (k.total_access||0).toLocaleString();
    document.getElementById('kn-src').textContent = Object.entries(k.by_source||{}).slice(0,3).map(([s,n])=>`${s}:${n}`).join(', ');
    // live market banner
    const mk = k.market || null;
    const lbPx = document.getElementById('lb-px');
    const lbPulse = document.getElementById('lb-pulse');
    if (mk) {
      document.getElementById('lb-sym').textContent = mk.sym + ' · live tape';
      lbPx.textContent = '$' + mk.mid.toLocaleString(undefined,{maximumFractionDigits:2});
      document.getElementById('lb-bidask').textContent =
        'bid $' + mk.bid.toFixed(2) + ' / ask $' + mk.ask.toFixed(2);
      document.getElementById('lb-spread').textContent = mk.spread_bps.toFixed(1) + ' bps';
      const age = k.market_age_s || 0;
      document.getElementById('lb-age').textContent = age.toFixed(1) + 's ago';
      lbPx.classList.toggle('stale', age > 30);
    }
    document.getElementById('lb-learn').textContent =
      (k.learn_hz||0).toFixed(2) + '/s · ' + (k.added_last_1m||0) + '/min · ' + (k.added_last_5m||0) + '/5min';
    if (k.swarm_text) {
      const sage = k.swarm_age_s || 0;
      document.getElementById('lb-swarm').textContent =
        (k.swarm_text||'').slice(0,55) + ' (' + Math.round(sage) + 's)';
    }
    // pulse if new entries arrived since last poll
    if (window.__lastTotal != null && k.total_entries > window.__lastTotal) {
      lbPulse.classList.remove('pulsing'); void lbPulse.offsetWidth;
      lbPulse.classList.add('pulsing');
    }
    window.__lastTotal = k.total_entries;
    window.__learnHz = k.learn_hz || 0;
    // backups
    const b = s.backups || {};
    document.getElementById('bk-count').textContent = b.count || 0;
    document.getElementById('bk-latest').textContent = (b.list && b.list[0]) ? b.list[0].name.slice(0,40) : '—';
    // recent knowledge — separate fetch to keep state lean
    if (Math.random() < 0.3) loadRecent();
    // 3D update
    if (s.viz && s.viz.neurons) updateNeuronStates(s.viz.neurons);
  } catch (e) { console.error('poll err', e); }
}

async function loadRecent() {
  try {
    const r = await fetch('/api/recent', { cache: 'no-store' });
    const items = await r.json();
    document.getElementById('recent').innerHTML = items.slice(0,12).map(it =>
      `<div class="entry"><div class="src">[${it.source||'?'}] ${new Date((it.timestamp||0)*1000).toISOString().slice(11,19)}Z</div>
       <div class="text">${(it.text||'').slice(0,150)}</div></div>`
    ).join('');
  } catch (e) {}
}

initThree();
animate();
poll();
loadRecent();
setInterval(poll, 1000);
</script>
</body></html>
"""


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def _send_json(self, payload, status=200):
        body = json.dumps(payload, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self):
        body = HTML.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_static(self, fs_path: Path, rewrite: bool = False) -> bool:
        """Serve a file from disk if it lives under ORIGINAL_DASH. Returns True if served.
        rewrite=True: rewrite absolute asset paths (`/css/...`, `/js/...`) to live under
        /original/, so the master dashboard's hard-coded absolute paths resolve here.
        """
        try:
            real = fs_path.resolve()
            root = ORIGINAL_DASH.resolve()
            if not str(real).startswith(str(root)):
                return False
            if not real.is_file():
                return False
            data = real.read_bytes()
            ctype = _MIME.get(real.suffix.lower(), "application/octet-stream")
            if rewrite and ctype.startswith(("text/", "application/javascript")):
                txt = data.decode("utf-8")
                # absolute /css/, /js/ → /original/css/, /original/js/
                txt = re.sub(r'(["\'])/(css|js)/', r'\1/original/\2/', txt)
                # api + ws too — they will 404 / fail to connect, but at least no
                # cross-host requests to the GIL-locked port 8889
                txt = re.sub(r"fetch\(\s*(['\"])/api/", r"fetch(\1/original/api/", txt)
                txt = re.sub(r"`ws://\$\{window\.location\.host\}/ws`",
                             r"`ws://${window.location.host}/original/ws`", txt)
                data = txt.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)
            return True
        except Exception:
            return False

    def _handle_ws(self) -> None:
        """Master-dashboard compatible WebSocket. Sends init then state every 1s."""
        key = self.headers.get("Sec-WebSocket-Key", "")
        if not key:
            self.send_error(400, "missing Sec-WebSocket-Key")
            return
        accept = _ws_accept_key(key)
        try:
            self.connection.sendall(
                b"HTTP/1.1 101 Switching Protocols\r\n"
                b"Upgrade: websocket\r\n"
                b"Connection: Upgrade\r\n"
                b"Sec-WebSocket-Accept: " + accept.encode("ascii") + b"\r\n\r\n"
            )
        except Exception:
            return
        try:
            init = json.dumps({"type": "init", "positions": build_master_init_positions()}, default=str)
            self.connection.sendall(_ws_text_frame(init.encode("utf-8")))
        except Exception:
            return
        # state push loop — best-effort; sendall errors close the connection
        while True:
            try:
                state = json.dumps({"type": "state", "data": build_master_state()}, default=str)
                self.connection.sendall(_ws_text_frame(state.encode("utf-8")))
            except (BrokenPipeError, ConnectionResetError, OSError):
                return
            except Exception:
                return
            time.sleep(1.0)

    def do_GET(self):
        try:
            p = self.path.split("?", 1)[0]
            # WS upgrade — both /ws (rewrite-bypassed if any) and /original/ws
            if p in ("/ws", "/original/ws"):
                upg = (self.headers.get("Upgrade") or "").lower()
                if "websocket" in upg:
                    self._handle_ws()
                    return
                self.send_error(400, "expected websocket upgrade")
                return
            if p in ("/", "/index.html"):
                self._send_html()
            elif p == "/api/state":
                self._send_json(gather_state())
            elif p == "/api/recent":
                self._send_json(read_recent_knowledge(20))
            elif p == "/api/regions":
                self._send_json(gather_region_summary())
            elif p == "/api/viz":
                self._send_json(gather_visualization_data())
            elif p == "/api/master_state":
                self._send_json(build_master_state())
            elif p == "/original" or p == "/original/":
                if not self._send_static(ORIGINAL_DASH / "index.html", rewrite=True):
                    self.send_error(404)
            elif p.startswith("/original/"):
                rel = p[len("/original/"):]
                if not self._send_static(ORIGINAL_DASH / rel, rewrite=True):
                    self.send_error(404)
            else:
                self.send_error(404)
        except Exception as e:
            try:
                self.send_error(500, f"{type(e).__name__}: {e}")
            except Exception:
                pass


class ThreadedTCPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def main() -> int:
    _gen_3d_positions_once()
    t = threading.Thread(target=_poll_regions_loop, daemon=True)
    t.start()
    print(f"[brain-insights v2] poll started, serving on 0.0.0.0:{PORT}", flush=True)
    with ThreadedTCPServer(("0.0.0.0", PORT), Handler) as srv:
        try:
            srv.serve_forever()
        except KeyboardInterrupt:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
