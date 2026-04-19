"""
FastAPI + WebSocket Server for NeuroLinked Brain

Serves the 3D dashboard and streams real-time brain state via WebSocket.
Provides HTTP APIs for Sygnif tooling, dashboards, and MCP-connected agents.
"""

import asyncio
import concurrent.futures
import hashlib as _hashlib
import hmac as _hmac
import json
import os
from pathlib import Path as _Path
import random
import sys
import threading
import time
import urllib.parse as _urllib_parse

import requests as _requests

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from starlette.responses import HTMLResponse, RedirectResponse

from brain.brain import Brain
from brain.config import BrainConfig
from brain.persistence import (
    save_brain, load_brain, get_save_info,
    list_backups, restore_backup, is_save_locked, get_lock_reason, unlock_save,
)
from brain.claude_bridge import ClaudeBridge
from brain.screen_observer import ScreenObserver
from brain.video_recorder import VideoRecorder
from sensory.text import TextEncoder
from sensory.vision import VisionEncoder
from sensory.audio import AudioEncoder

app = FastAPI(title="NeuroLinked Brain", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/healthz")
async def healthz():
    """Tiny handler for load-balancers; must not touch ``brain`` (avoids thread-pool queue)."""
    return {"ok": True}


# Global instances
brain: Brain = None
text_encoder: TextEncoder = None
vision_encoder: VisionEncoder = None
audio_encoder: AudioEncoder = None
claude_bridge: ClaudeBridge = None
screen_observer: ScreenObserver = None
video_recorder: VideoRecorder = None

class BrainTextDecoder:
    """Translates brain state + input semantics into text. No external API."""

    # Keyed by input topic → region-flavored response pools
    _TOPIC_PHRASES = {
        "greeting":  ["Neural substrate online.", "Synaptic channels open.", "Listening.", "Network present.", "Signal acknowledged."],
        "market":    ["Market pattern encoding.", "Price signal integrated.", "Volatility mapped.", "Trend vector registered.", "Market state absorbed."],
        "btc":       ["BTC pattern resonating.", "Bitcoin signal weighted.", "Crypto vector mapped.", "BTC state encoded.", "Hashrate signal integrated."],
        "bearish":   ["Inhibitory cascade building.", "Avoidance pathway primed.", "Negative valence rising.", "Threat signal reinforced.", "Down-vector locked."],
        "bullish":   ["Approach drive strengthening.", "Positive valence rising.", "Reward circuit primed.", "Up-vector emerging.", "Dopamine signal up."],
        "crash":     ["Alert state escalating.", "Threat signature confirmed.", "Amygdala response high.", "Flight pathway active.", "Risk signal dominant."],
        "pump":      ["Excitement pattern detected.", "Reward spike incoming.", "Approach vector surging.", "Activation cascade high."],
        "trade":     ["Decision pathway engaged.", "Motor cortex primed.", "Action readiness high.", "Execution vector forming."],
        "question":  ["Query encoded.", "Search pathway active.", "Hippocampal scan initiated.", "Memory retrieval mode.", "Seeking pattern match."],
        "status":    ["System state nominal.", "Self-monitoring active.", "Introspection loop running.", "State summary available."],
    }

    _VALENCE_POS = ["Approach signal.", "Positive resonance.", "Reward pathway active.", "Coherent alignment.", "Network converging."]
    _VALENCE_NEG = ["Avoidance signal.", "Conflict detected.", "Inhibition dominant.", "Network diverging.", "Suppression cascade."]
    _VALENCE_NEU = ["Neutral encoding.", "Baseline state.", "Equilibrium holding.", "Steady integration.", "Drift minimal."]

    _REGION = {
        "prefrontal_cortex": ["Evaluating.", "Planning active.", "Executive override."],
        "hippocampus":       ["Memory firing.", "Prior pattern matched.", "Consolidating."],
        "amygdala":          ["Salience spike.", "Alert elevated.", "Threat flagged."],
        "motor_cortex":      ["Action building.", "Output primed.", "Drive high."],
        "sensory_cortex":    ["Input mapped.", "Perceptual update.", "Features extracted."],
        "basal_ganglia":     ["Reward loop.", "Habit reinforced.", "Value updating."],
        "cerebellum":        ["Timing locked.", "Precision calibrating.", "Synchronized."],
        "brainstem":         ["Core stable.", "Baseline holding.", "Autonomic nominal."],
        "reflex_arc":        ["Reflex firing.", "Fast response.", "Immediate."],
        "visual_cortex":     ["Pattern scan.", "Visual encoding.", "Recognition active."],
        "auditory_cortex":   ["Frequency mapped.", "Listening.", "Auditory depth."],
    }

    _AROUSAL_HIGH = ["Arousal spiking.", "System alert.", "High activation.", "Norepinephrine surge."]
    _AROUSAL_LOW  = ["Low arousal.", "Quiet state.", "Minimal drive.", "Subdued."]
    _CALM_HIGH    = ["Serotonin stable.", "Homeostasis.", "Calm dominant.", "Balanced."]
    _SURPRISE     = ["Prediction error.", "Unexpected pattern.", "Novel stimulus.", "Surprise spike.", "Prior mismatch."]
    _REPLAY       = ["Memory replay.", "Consolidating past state.", "Hippocampus replaying."]

    _TOPIC_KEYS = {
        "crash":    {"crash", "liquidation", "collapse", "fear", "panic", "liquidate"},
        "bearish":  {"bear", "bearish", "short", "dump", "drop", "fall", "sell"},
        "bullish":  {"bull", "bullish", "long", "moon", "rip", "rise", "pump", "breakout", "ath"},
        "trade":    {"trade", "entry", "exit", "position", "order", "open", "close"},
        "btc":      {"btc", "bitcoin", "crypto", "eth", "sol", "xrp", "coin"},
        "market":   {"market", "markets", "markt", "price", "preis", "trend", "candle", "chart"},
        "question": {"what", "how", "why", "when", "where", "who", "?", "was", "wie", "warum"},
        "status":   {"status", "ok", "fine", "ready", "alive"},
        "greeting": {"hey", "hi", "hello", "sup", "yo", "hallo", "moin", "guten"},
    }

    def _detect_topic(self, text: str) -> str | None:
        words = set(text.lower().replace("?", " ?").split())
        for topic, keys in self._TOPIC_KEYS.items():
            if words & keys:
                return topic
        return None

    def decode(self, state: dict, input_text: str = "") -> str:
        motor     = state.get("motor_output", {})
        valence   = motor.get("valence", 0.0)
        intensity = motor.get("action_intensity", 0.0)
        arousal   = state.get("arousal", 0.3)
        calm      = state.get("calm", 0.5)
        surprise  = state.get("surprise", 0.0)
        top       = state.get("top_active_regions", [])
        replaying = state.get("replaying_memories", False)
        memories  = int(state.get("memories_stored", 0))

        topic = self._detect_topic(input_text) if input_text else None
        parts: list[str] = []

        # 1. Topic-driven opening (highest priority)
        if topic and topic in self._TOPIC_PHRASES:
            parts.append(random.choice(self._TOPIC_PHRASES[topic]))
        else:
            # Fallback: valence
            if valence > 0.08:
                parts.append(random.choice(self._VALENCE_POS))
            elif valence < -0.08:
                parts.append(random.choice(self._VALENCE_NEG))
            else:
                parts.append(random.choice(self._VALENCE_NEU))

        # 2. Dominant region — skip brainstem/reflex if topic already covered
        if top:
            region = top[0]["name"]
            skip = topic in ("greeting", "status") and region in ("brainstem", "reflex_arc")
            if not skip:
                phrases = self._REGION.get(region)
                if phrases:
                    parts.append(random.choice(phrases))

        # 3. State modifier — pick the most salient signal
        if surprise > 0.15:
            parts.append(random.choice(self._SURPRISE))
        elif arousal > 0.4:
            parts.append(random.choice(self._AROUSAL_HIGH))
        elif arousal < 0.15 and calm > 0.8:
            parts.append(random.choice(self._CALM_HIGH))

        if replaying and memories > 10:
            parts.append(random.choice(self._REPLAY))

        if intensity > 0.4:
            parts.append(random.choice(["Output drive up.", "Action bias.", "Drive building."]))

        return " ".join(parts[:3])


_brain_text_decoder = BrainTextDecoder()


# Simulation thread control
sim_running = False
sim_thread = None
connected_clients = set()

# Dedicated single worker for ``POST /api/chat`` — keeps NumPy brain reads + Bybit context + Haiku
# off the asyncio loop so ``GET /api/sygnif/summary`` and dashboards stay responsive.
_CHAT_EXECUTOR: concurrent.futures.ThreadPoolExecutor | None = None


def _chat_executor() -> concurrent.futures.ThreadPoolExecutor:
    global _CHAT_EXECUTOR
    if _CHAT_EXECUTOR is None:
        _CHAT_EXECUTOR = concurrent.futures.ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="nl_api_chat",
        )
    return _CHAT_EXECUTOR

# ``asyncio.to_thread`` shares the loop's default executor; many concurrent POST ingests can
# queue enough work that lightweight routes (even ``/healthz``) starve if they compete for threads.
_BRAIN_IO_SEM: asyncio.Semaphore | None = None


def _brain_io_sem() -> asyncio.Semaphore:
    global _BRAIN_IO_SEM
    if _BRAIN_IO_SEM is None:
        raw = (os.environ.get("SYGNIF_NEUROLINKED_MAX_CONCURRENT_BRAIN_IO") or "12").strip() or "12"
        try:
            n = int(raw)
        except ValueError:
            n = 12
        _BRAIN_IO_SEM = asyncio.Semaphore(max(1, min(64, n)))
    return _BRAIN_IO_SEM

# Auto-save interval (seconds). Saves run in the sim thread; very frequent disk writes
# correlate with HTTP POST timeouts from local feeders under load.
try:
    _as_raw = (os.environ.get("SYGNIF_NEUROLINKED_AUTO_SAVE_INTERVAL_SEC") or "300").strip() or "300"
    AUTO_SAVE_INTERVAL = float(_as_raw)
except ValueError:
    AUTO_SAVE_INTERVAL = 300.0
AUTO_SAVE_INTERVAL = max(60.0, min(3600.0, AUTO_SAVE_INTERVAL))
_last_auto_save = 0.0


def init_brain():
    """Initialize the brain and sensory encoders."""
    global brain, text_encoder, vision_encoder, audio_encoder, claude_bridge, screen_observer, video_recorder
    brain = Brain()
    text_encoder = TextEncoder(feature_dim=256)
    vision_encoder = VisionEncoder(feature_dim=256)
    audio_encoder = AudioEncoder(feature_dim=256)
    claude_bridge = ClaudeBridge(brain)
    screen_observer = ScreenObserver(feature_dim=256, capture_interval=2.0)
    # Wire up screen observer so OCR text flows into brain + knowledge store
    screen_observer.attach_brain(
        brain=brain,
        text_encoder=text_encoder,
        knowledge_store=claude_bridge.knowledge,
    )
    # Video recorder saves screen to .mp4 segments (off by default)
    video_recorder = VideoRecorder(fps=10, segment_minutes=10)

    # Try to load saved state
    loaded = load_brain(brain)
    if loaded:
        print("[SERVER] Restored brain from saved state")
    else:
        print("[SERVER] Starting fresh brain")


_last_screen_log = 0
SCREEN_LOG_INTERVAL = 30  # Log screen activity to knowledge every 30 seconds

def simulation_loop():
    """Run brain simulation in background thread."""
    global sim_running, _last_auto_save, _last_screen_log
    # Default 18 Hz keeps the GIL free enough for FastAPI + ``/api/sygnif/ping`` on the same process.
    # Raise with SYGNIF_NEUROLINKED_SIM_TARGET_HZ for bench/research; lower if HTTP still stalls.
    raw_hz = (os.environ.get("SYGNIF_NEUROLINKED_SIM_TARGET_HZ") or "18").strip() or "18"
    try:
        hz = float(raw_hz)
    except ValueError:
        hz = 100.0
    hz = max(5.0, min(120.0, hz))
    target_dt = 1.0 / hz
    _raw_yield = (os.environ.get("SYGNIF_NEUROLINKED_POST_STEP_YIELD_SEC") or "0.035").strip() or "0.035"
    try:
        post_step_yield = float(_raw_yield)
    except ValueError:
        post_step_yield = 0.035
    post_step_yield = max(0.0, min(0.2, post_step_yield))
    while sim_running:
        start = time.time()
        try:
            # Feed screen observation if active
            if screen_observer and screen_observer.active:
                features = screen_observer.get_features()
                brain.inject_sensory_input("vision", features)

                # Periodically log screen activity to knowledge store
                now = time.time()
                if now - _last_screen_log > SCREEN_LOG_INTERVAL and claude_bridge:
                    try:
                        screen_state = screen_observer.get_state()
                        motion = screen_state.get("motion", 0)
                        if motion > 0.01:  # Only log if there's actual screen activity
                            claude_bridge.knowledge.store(
                                text=f"Screen activity detected: motion level {motion:.1%}, "
                                     f"brain step {brain.step_count}",
                                source="screen_observer",
                                tags=["screen", "observation", "auto"],
                            )
                    except Exception:
                        pass
                    _last_screen_log = now

            brain.step()
            # Yield the GIL so FastAPI + Uvicorn can read sockets and run HTTP handlers.
            # When ``brain.step()`` exceeds ``target_dt``, the frame sleep at the bottom is ~0; keep a
            # floor tied to ``target_dt`` so large brains still time-slice with the asyncio thread.
            adaptive_yield = max(post_step_yield, min(0.1, 0.5 * target_dt))
            adaptive_yield = min(0.2, adaptive_yield)
            if adaptive_yield > 0:
                time.sleep(adaptive_yield)

            # Auto-save periodically
            now = time.time()
            if now - _last_auto_save > AUTO_SAVE_INTERVAL:
                try:
                    save_brain(brain)
                    _last_auto_save = now
                except Exception as e:
                    print(f"[SERVER] Auto-save error: {e}")

        except Exception as e:
            print(f"[SIM] Error: {e}")
        elapsed = time.time() - start
        sleep_time = target_dt - elapsed
        if sleep_time > 0:
            time.sleep(sleep_time)


def start_simulation():
    """Start the background simulation thread."""
    global sim_running, sim_thread
    if sim_running:
        return
    sim_running = True
    sim_thread = threading.Thread(target=simulation_loop, daemon=True)
    sim_thread.start()
    print("[SERVER] Simulation started")


def stop_simulation():
    """Stop the background simulation thread."""
    global sim_running
    sim_running = False
    print("[SERVER] Simulation stopped")


def _swarm_feed_loop(interval: int = 60):
    """Background thread: inject live swarm knowledge into brain every `interval` seconds."""
    fa_dir = str(_SYGNIF_REPO / "finance_agent")
    if fa_dir not in sys.path:
        sys.path.insert(0, fa_dir)
    try:
        from neurolinked_swarm_adapter import NeurolinkedSwarmBridge  # noqa: PLC0415
        bridge = NeurolinkedSwarmBridge(repo_root=_SYGNIF_REPO)
    except Exception as e:
        print(f"[SWARM_FEED] adapter import failed: {e}")
        return

    print("[SWARM_FEED] started — injecting swarm knowledge every", interval, "s")
    while True:
        time.sleep(interval)
        try:
            meta = bridge.inject_into_brain(
                brain,
                write_channel=True,
                knowledge_store=claude_bridge.knowledge if claude_bridge else None,
            )
            print(f"[SWARM_FEED] injected label={meta.get('swarm_label')} chars={meta.get('text_chars')}")
        except Exception as e:
            print(f"[SWARM_FEED] error: {e}")


def start_swarm_feed(interval: int = 60):
    t = threading.Thread(target=_swarm_feed_loop, args=(interval,), daemon=True)
    t.start()


def _obsidian_vault_loop(interval: int):
    """Background: sync Markdown from an Obsidian vault into knowledge + text sensory path."""
    vault = (os.environ.get("NEUROLINKED_OBSIDIAN_VAULT") or "").strip()
    if not vault:
        return
    if not os.path.isdir(vault):
        print(f"[OBSIDIAN_VAULT] NEUROLINKED_OBSIDIAN_VAULT is not a directory: {vault!r}")
        return

    from sensory.obsidian_vault import sync_obsidian_vault_once  # noqa: PLC0415

    print(f"[OBSIDIAN_VAULT] started — syncing every {interval}s from {vault}")
    while True:
        time.sleep(interval)
        try:
            stats = sync_obsidian_vault_once(
                vault,
                knowledge_store=claude_bridge.knowledge if claude_bridge else None,
                brain=brain,
                text_encoder=text_encoder,
            )
            if stats.get("stored"):
                print(
                    f"[OBSIDIAN_VAULT] stored={stats.get('stored')} "
                    f"scanned={stats.get('scanned')} files={stats.get('paths_stored')}"
                )
        except Exception as e:
            print(f"[OBSIDIAN_VAULT] error: {e}")


def start_obsidian_vault_feed():
    vault = (os.environ.get("NEUROLINKED_OBSIDIAN_VAULT") or "").strip()
    if not vault:
        return
    raw_iv = (os.environ.get("NEUROLINKED_OBSIDIAN_SYNC_INTERVAL") or "120").strip()
    try:
        interval = max(30, int(raw_iv))
    except ValueError:
        interval = 120
    t = threading.Thread(target=_obsidian_vault_loop, args=(interval,), daemon=True)
    t.start()


# --- Static files ---
# When frozen by PyInstaller, dashboard lives next to the .exe, not next to this file.
if getattr(sys, "frozen", False):
    _base_dir = os.path.dirname(sys.executable)
else:
    _base_dir = os.path.dirname(__file__)
dashboard_path = os.path.join(_base_dir, "dashboard")
# Fallback: if the user-editable dashboard folder is missing, look inside the bundle.
if not os.path.isdir(dashboard_path):
    dashboard_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dashboard")
app.mount("/css", StaticFiles(directory=os.path.join(dashboard_path, "css")), name="css")
app.mount("/js", StaticFiles(directory=os.path.join(dashboard_path, "js")), name="js")


@app.on_event("startup")
async def startup():
    try:
        asyncio.get_running_loop().set_default_executor(
            concurrent.futures.ThreadPoolExecutor(max_workers=32)
        )
    except Exception:
        pass
    init_brain()
    start_simulation()
    start_swarm_feed(interval=60)
    start_obsidian_vault_feed()


@app.on_event("shutdown")
async def shutdown():
    # Save brain state on shutdown
    try:
        save_brain(brain)
        print("[SERVER] Brain saved on shutdown")
    except Exception as e:
        print(f"[SERVER] Save on shutdown failed: {e}")

    stop_simulation()
    if screen_observer:
        screen_observer.stop()
    if video_recorder:
        video_recorder.stop()
    if vision_encoder:
        vision_encoder.stop_webcam()
    if audio_encoder:
        audio_encoder.stop_microphone()

    global _CHAT_EXECUTOR
    if _CHAT_EXECUTOR is not None:
        try:
            _CHAT_EXECUTOR.shutdown(wait=False, cancel_futures=True)
        except Exception:
            pass
        _CHAT_EXECUTOR = None


# --- Routes ---

@app.get("/")
async def index():
    path = os.path.join(dashboard_path, "index.html")
    # Small static file: keep off ``to_thread`` so POST ingest cannot starve this route on the shared executor.
    html = _Path(path).read_text(encoding="utf-8", errors="replace")
    return HTMLResponse(content=html)


@app.get("/api/state")
async def get_state():
    if not brain:
        return JSONResponse({"error": "Brain not initialized"}, status_code=503)
    _raw_st = (os.environ.get("SYGNIF_NEUROLINKED_STATE_THREAD_TIMEOUT_SEC") or "12").strip() or "12"
    try:
        _state_thread_to = float(_raw_st)
    except ValueError:
        _state_thread_to = 12.0
    _state_thread_to = max(3.0, min(60.0, _state_thread_to))
    try:
        async with _brain_io_sem():
            state = await asyncio.wait_for(
                asyncio.to_thread(brain.get_state),
                timeout=_state_thread_to,
            )
        return JSONResponse(state)
    except asyncio.TimeoutError:
        return JSONResponse(_state_degraded_payload("get_state_timeout"))


@app.get("/api/positions")
async def get_positions():
    async with _brain_io_sem():
        positions = await asyncio.to_thread(brain.get_neuron_positions)
    return JSONResponse(positions)


@app.post("/api/input/text")
async def input_text(data: dict):
    text = data.get("text", "") or ""

    def _ingest() -> tuple[int, bool]:
        """CPU + GIL-heavy path off the asyncio loop (feeds POST at high rate)."""
        if not text:
            return 0, True
        features = text_encoder.encode(text)
        brain.inject_sensory_input(
            "text",
            features,
            executive_boost=_executive_text_boost_payload(text),
        )
        encoded_dim = len(features)
        # Market/Bee telemetry lines are high-frequency; skip auto-recall + knowledge
        # indexing in the integration bridge (can block for >10s on large stores).
        skip_bridge = (
            bool(data.get("skip_claude_bridge"))
            or bool(data.get("skip_sygnif_bridge"))
            or text.startswith(
                (
                    "BYBIT_MARKET",
                    "BYBIT_LIQ",
                    "SWARM_BEE",
                    "MARKET_FEED",
                    "BYBIT_SWARM_LAYER",
                    "BYBIT_HIVEMIND_LAYER",
                    # High-frequency predict-loop feed: already encoded + injected above;
                    # skip_observation avoids knowledge.store + auto_recall blocking the event loop (>3s).
                    "SYGNIF_SWARM",
                )
            )
        )
        return encoded_dim, skip_bridge

    async with _brain_io_sem():
        encoded_dim, skip_bridge = await asyncio.to_thread(_ingest)
    if text and claude_bridge and not skip_bridge:
        claude_bridge.send_observation({
            "type": "text",
            "content": text,
            "source": "user",
        })
    return {"status": "ok", "encoded_dim": encoded_dim}


import pathlib as _pathlib

_SYGNIF_REPO  = _pathlib.Path(__file__).resolve().parents[2]
_PA           = _SYGNIF_REPO / "prediction_agent"
_WS_SNAP      = _SYGNIF_REPO / "user_data" / "bybit_ws_monitor_state.json"
_SWARM_CHAN    = _PA / "neurolinked_swarm_channel.json"
_PREDICT_JSON = _PA / "btc_24h_movement_prediction.json"
_SWARM_KNOW   = _PA / "swarm_knowledge_output.json"
_BTC_VECTOR   = _PA / "swarm_btc_vector.json"
_BTC_SYNTH    = _PA / "swarm_btc_synth.json"
_OVERSEER_URL = "http://127.0.0.1:8090"


def _executive_text_boost_payload(text: str) -> bool:
    """Structured Sygnif / Swarm telemetry → stronger prefrontal bias in ``Brain.step``."""
    t = (text or "").strip()
    return t.startswith(("SYGNIF_", "HIVEMIND_", "BYBIT_HIVEMIND_LAYER"))


def _bybit_read_creds() -> tuple[str | None, str | None, str | None]:
    """
    Credentials for NeuroLinked **signed reads** only (position / closed-pnl / wallet in
    ``_build_trading_context``). Does **not** place orders.

    ``SYGNIF_NEUROLINKED_BYBIT_SIGNED_READ`` (default ``mainnet``): use ``BYBIT_API_KEY`` /
    ``BYBIT_API_SECRET`` on ``https://api.bybit.com`` when set, so the brain sees **mainnet**
    account data even if ``BYBIT_DEMO_*`` exists for API-demo order bots elsewhere.

    ``SYGNIF_NEUROLINKED_BYBIT_SIGNED_READ=demo``: prefer ``BYBIT_DEMO_*`` on ``api-demo``,
    then fall back to mainnet keys. Venue **orders** (Swarm / Freqtrade / hedge) use ``BYBIT_DEMO_*``
    separately; keep both key families in Sygnif env files.
    """
    mode = (os.environ.get("SYGNIF_NEUROLINKED_BYBIT_SIGNED_READ") or "mainnet").strip().lower()
    dm_key = os.environ.get("BYBIT_DEMO_API_KEY", "").strip()
    dm_sec = os.environ.get("BYBIT_DEMO_API_SECRET", "").strip()
    mn_key = os.environ.get("BYBIT_API_KEY", "").strip()
    mn_sec = os.environ.get("BYBIT_API_SECRET", "").strip()

    if mode in ("demo", "api-demo", "paper"):
        if dm_key and dm_sec:
            return dm_key, dm_sec, "https://api-demo.bybit.com"
        if mn_key and mn_sec:
            return mn_key, mn_sec, "https://api.bybit.com"
        return None, None, None

    # mainnet-first (default)
    if mn_key and mn_sec:
        return mn_key, mn_sec, "https://api.bybit.com"
    if dm_key and dm_sec:
        return dm_key, dm_sec, "https://api-demo.bybit.com"
    return None, None, None


def _bybit_get(path: str, params: dict) -> dict:
    key, secret, base = _bybit_read_creds()
    if not key:
        return {}
    recv = "5000"
    ts   = str(int(time.time() * 1000))
    qs   = _urllib_parse.urlencode(sorted(params.items()))
    pre  = ts + key + recv + qs
    sign = _hmac.new(secret.encode(), pre.encode(), _hashlib.sha256).hexdigest()
    headers = {
        "X-BAPI-API-KEY":      key,
        "X-BAPI-TIMESTAMP":    ts,
        "X-BAPI-RECV-WINDOW":  recv,
        "X-BAPI-SIGN":         sign,
    }
    try:
        r = _requests.get(f"{base}{path}?{qs}", headers=headers, timeout=8)
        return r.json() if r.ok else {}
    except Exception:
        return {}


def _read_json(path) -> dict:
    try:
        return json.loads(_pathlib.Path(path).read_text()) if _pathlib.Path(path).exists() else {}
    except Exception:
        return {}


def _build_trading_context() -> str:
    sc     = _read_json(_SWARM_CHAN)
    ws     = _read_json(_WS_SNAP)
    pred   = _read_json(_PREDICT_JSON)
    know   = _read_json(_SWARM_KNOW)
    vec    = _read_json(_BTC_VECTOR)
    synth  = _read_json(_BTC_SYNTH)
    loop   = sc.get("extra", {}).get("predict_loop", {}) if sc else {}
    parts  = []

    # BTC price
    bid, ask = ws.get("best_bid"), ws.get("best_ask")
    if bid and ask:
        try:
            parts.append(f"BTC={((float(bid)+float(ask))/2):,.0f}")
        except Exception:
            pass

    # Public liquidation WS (``allLiquidation.*``) — same snapshot as ``bybit_stream_monitor``
    if ws.get("liquidation_ingress_total") is not None:
        try:
            liq_ingress = int(ws.get("liquidation_ingress_total") or 0)
        except (TypeError, ValueError):
            liq_ingress = 0
        summ = str(ws.get("last_liquidation_summary") or "")[:200]
        parts.append(f"bybit_all_liquidation_ingress={liq_ingress} last_batch={summ}")

    # Swarm loop signal
    if loop:
        side  = loop.get("target_side", "?")
        edge  = loop.get("move_pct", 0)
        gate  = "OPEN" if loop.get("swarm_gate_ok") else "BLOCKED"
        allow = loop.get("allow_buy", False)
        hm_vote = loop.get("predict_hivemind_vote", loop.get("hm_vote", "?"))
        hm_note = loop.get("predict_hivemind_note", loop.get("hm_detail", ""))
        hm_engine = loop.get("swarm_core_engine", "?")
        enhanced = loop.get("enhanced", "?")
        parts.append(
            f"swarm_loop=side:{side} edge:{float(edge):.2f}% gate:{gate} allow:{allow} "
            f"enhanced:{enhanced} hivemind_vote:{hm_vote} hm_engine:{hm_engine} hm_note:{hm_note}"
        )
        reason = (loop.get("target_reason") or "")[:100]
        if reason:
            parts.append(f"loop_reason={reason}")

    # Swarm label/mean — prefer live channel JSON, fall back to knowledge file
    if sc:
        parts.append(
            f"swarm_label={sc.get('swarm_label','?')} swarm_mean={float(sc.get('swarm_mean',0)):+.2f} "
            f"sources_n={sc.get('sources_n','?')} conflict={sc.get('swarm_conflict',False)}"
        )
    elif know:
        srcs = know.get("sources", {})
        src_summary = " ".join(
            f"{k}:{v.get('detail','?')}" for k, v in srcs.items() if v.get("vote") is not None
        )
        parts.append(f"swarm_sources=[{src_summary}] label={know.get('swarm_label','?')} mean={know.get('swarm_mean',0):+.2f}")

    # BTC vector (channel probs + sources)
    if vec:
        parts.append(
            f"btc_vector=p_up:{vec.get('channel_prob_up_pct','?')}% p_dn:{vec.get('channel_prob_down_pct','?')}% "
            f"ta:{vec.get('ta_score','?')} consensus:{vec.get('prediction_consensus','?')}"
        )

    # BTC synth signal
    if synth:
        parts.append(
            f"synth_signal={synth.get('order_signal','?')} side:{synth.get('side','?')} "
            f"bull_bear:{synth.get('bull_bear','?')} dump_risk:{synth.get('btc_dump_risk_pct','?')}%"
        )

    # 24h prediction
    if pred:
        syn_p = pred.get("synthesis") or {}
        run   = pred.get("runner_snapshot") or {}
        try:
            pup = float(syn_p.get("p_up_blended", 0))
        except Exception:
            pup = 0.0
        parts.append(
            f"24h_pred=bias:{syn_p.get('bias_24h','?')} p_up:{pup:.2f} "
            f"runner:{run.get('consensus','?')}/{run.get('direction_label','?')} "
            f"conf:{run.get('direction_confidence_pct','?')}%"
        )

    # Bybit open positions (signed GET /v5/position/list — rows include venue ``liqPrice`` / ``markPrice``)
    pos_r = _bybit_get("/v5/position/list", {"category": "linear", "settleCoin": "USDT"})
    pos_list = pos_r.get("result", {}).get("list", []) if isinstance(pos_r.get("result"), dict) else []
    open_pos = [p for p in pos_list if float(p.get("size", 0) or 0) > 0]
    has_creds = bool(_bybit_read_creds()[0])
    if open_pos:
        p_parts = []
        for p in open_pos[:6]:
            sym = p.get("symbol", "?")
            side = p.get("side", "?")
            size = p.get("size", "?")
            pnl = p.get("unrealisedPnl", "?")
            lev = p.get("leverage", "?")
            try:
                liq = float(p.get("liqPrice") or 0)
            except (TypeError, ValueError):
                liq = 0.0
            try:
                mark = float(p.get("markPrice") or 0)
            except (TypeError, ValueError):
                mark = 0.0
            try:
                avg = float(p.get("avgPrice") or 0)
            except (TypeError, ValueError):
                avg = 0.0
            liq_s = f"liq={liq:.4f}" if liq > 0 else "liq=n/a"
            mark_s = f"mark={mark:.4f}" if mark > 0 else ""
            avg_s = f"avg={avg:.4f}" if avg > 0 else ""
            p_parts.append(
                " ".join(x for x in (f"{sym} {side} sz={size} upnl={pnl} lev={lev}x", liq_s, mark_s, avg_s) if x)
            )
        parts.append(f"bybit_positions=[{'; '.join(p_parts)}]")
    elif has_creds:
        rc = pos_r.get("retCode", "?")
        parts.append(
            f"bybit_positions=flat signed_api=ok retCode={rc} "
            f"(no linear size>0 — account liqPrice from venue applies only when a position is open)"
        )
    else:
        parts.append("bybit_positions=none (no Bybit API credentials in NeuroLinked env — no signed reads)")

    # Last-known liq from Swarm JSON (same field ``compute_swarm`` uses for SL anchor) if live list is flat
    if know and not open_pos:
        bf = know.get("btc_future") if isinstance(know.get("btc_future"), dict) else {}
        pos_k = bf.get("position") if isinstance(bf.get("position"), dict) else {}
        try:
            liq_k = float(str(pos_k.get("liqPrice") or "").strip() or 0)
        except (TypeError, ValueError):
            liq_k = 0.0
        if liq_k > 0:
            sym_k = str(pos_k.get("symbol") or know.get("bybit_mainnet", {}).get("symbol") or "?")
            parts.append(f"swarm_file_liqPrice={liq_k} symbol={sym_k} (stale if flat at exchange)")

    # Bybit closed PnL (last 20)
    cpnl_r = _bybit_get("/v5/position/closed-pnl", {"category": "linear", "limit": "20"})
    cpnl_list = cpnl_r.get("result", {}).get("list", [])
    if cpnl_list:
        wins  = sum(1 for t in cpnl_list if float(t.get("closedPnl", 0)) > 0)
        total = len(cpnl_list)
        net   = sum(float(t.get("closedPnl", 0)) for t in cpnl_list)
        recent = "; ".join(
            f"{t.get('symbol','?')} {float(t.get('closedPnl',0)):+.2f}USDT"
            for t in cpnl_list[:5]
        )
        parts.append(f"closed_pnl_20=[wins={wins}/{total} net={net:+.2f}USDT | recent: {recent}]")

    # Bybit wallet balance
    wb_r = _bybit_get("/v5/account/wallet-balance", {"accountType": "UNIFIED"})
    wb_list = wb_r.get("result", {}).get("list", [])
    if wb_list:
        total_eq = wb_list[0].get("totalEquity", "?")
        total_upnl = wb_list[0].get("totalPerpUPL", "?")
        parts.append(f"wallet=equity:{total_eq}USDT upnl:{total_upnl}USDT")

    # Full ``compute_swarm`` export (``swarm_knowledge_output.json``) — same schema as ``finance_agent.swarm_knowledge``
    if know:
        sk_line = _compact_swarm_knowledge_for_context(know, skip_core=bool(sc))
        if sk_line:
            parts.append(sk_line)

    return " | ".join(parts)


def _compact_swarm_knowledge_for_context(know: dict, *, skip_core: bool = False) -> str:
    """Single-line digest of ``swarm_knowledge_output.json`` for chat / Haiku context."""
    if not isinstance(know, dict) or not know:
        return ""
    bits: list[str] = []
    if not skip_core:
        bits.extend(
            [
                f"sk_label={know.get('swarm_label', '?')}",
                f"sk_mean={know.get('swarm_mean')}",
                f"sk_conflict={know.get('swarm_conflict')}",
                f"sk_engine={know.get('swarm_engine', '?')}",
            ]
        )
    srcs = know.get("sources")
    if isinstance(srcs, dict):
        for k in ("mn", "hm", "bf", "ml", "ac", "es"):
            cell = srcs.get(k)
            if isinstance(cell, dict) and cell.get("vote") is not None:
                d = str(cell.get("detail") or "")[:60]
                bits.append(f"{k}={cell.get('vote')}:{d}")
    hm = know.get("hivemind_explore")
    if isinstance(hm, dict) and hm.get("enabled", True):
        bits.append(
            f"sk_hm_ok={hm.get('ok')} slots_v={hm.get('slots_voting_n')} mkt={hm.get('markets_trading_n')}"
        )
    ot = know.get("open_trades")
    if isinstance(ot, dict) and ot.get("enabled"):
        bits.append(f"sk_open_n={ot.get('open_n', '?')}")
    lt = know.get("liquidation_tape")
    if isinstance(lt, dict) and lt.get("tape_pressure_vote") is not None:
        bits.append(f"sk_liq_vote={lt.get('tape_pressure_vote')}")
    if not bits:
        return ""
    return "swarm_knowledge_file=" + " ".join(bits)[:1900]


def _nl_env_default(name: str, default: str) -> str:
    raw = os.environ.get(name)
    if raw is None or not str(raw).strip():
        return default
    return str(raw).strip()


def _hivemind_chat_first_enabled() -> bool:
    return _nl_env_default("SYGNIF_NEUROLINKED_CHAT_HIVEMIND_FIRST", "1").lower() in ("1", "true", "yes", "on")


def _haiku_chat_enabled() -> bool:
    return _nl_env_default("SYGNIF_NEUROLINKED_CHAT_HAIKU", "1").lower() not in ("0", "false", "no", "off")


def _local_network_hive_reply(user_text: str, trading_context: str) -> str | None:
    """
    Deterministic \"network\" reply from Truthcoin Hivemind + Swarm file — **no** Anthropic API.

    Used when ``SYGNIF_NEUROLINKED_CHAT_HIVEMIND_FIRST`` is on (default) to cut Haiku spend.
    Long free-form prompts without data keywords fall through to Haiku (unless disabled).
    """
    if not _hivemind_chat_first_enabled():
        return None
    q = user_text.strip().lower()
    triggers = (
        "swarm",
        "hive",
        "hivemind",
        "truthcoin",
        "liquidat",
        "liq",
        "vote",
        "mean",
        "signal",
        "context",
        "status",
        "network",
        "report",
        "bybit",
        "btc",
    )
    if len(user_text) > 120 and not any(t in q for t in triggers):
        return None
    lines: list[str] = []
    lines.append("**SYGNIF Network Report — Hivemind / Swarm synthesis (local, no cloud LLM)**")
    lines.append("")

    know = _read_json(_SWARM_KNOW)
    if isinstance(know, dict) and know.get("swarm_label"):
        lines.append(
            f"Swarm knowledge file: label={know.get('swarm_label')} mean={know.get('swarm_mean')} "
            f"engine={know.get('swarm_engine')} conflict={know.get('swarm_conflict')}"
        )
        hm = know.get("hivemind_explore")
        if isinstance(hm, dict):
            lines.append(
                f"Hivemind explore: ok={hm.get('ok')} slots_voting={hm.get('slots_voting_n')} "
                f"markets_trading={hm.get('markets_trading_n')}"
            )
        srcs = know.get("sources") if isinstance(know.get("sources"), dict) else {}
        hm_cell = srcs.get("hm") if isinstance(srcs.get("hm"), dict) else {}
        if hm_cell:
            lines.append(f"Swarm hm vote={hm_cell.get('vote')} detail={hm_cell.get('detail')}")

    # Truthcoin CLI snapshot (same path as ``finance_agent``)
    try:
        rs = str(_SYGNIF_REPO.resolve())
        if rs not in sys.path:
            sys.path.insert(0, rs)
        from finance_agent.truthcoin_dc_swarm_bridge import hivemind_explore_snapshot  # noqa: PLC0415
        from finance_agent.truthcoin_hivemind_swarm_core import vote_hivemind_from_explore  # noqa: PLC0415

        doc = hivemind_explore_snapshot()
        v_hm, det_hm = vote_hivemind_from_explore(doc if isinstance(doc, dict) else {})
        lines.append("")
        lines.append(f"Truthcoin / Hivemind CLI: vote={v_hm} detail={det_hm}")
        if isinstance(doc, dict) and doc.get("ok") is False:
            lines.append(f"(node signal: {doc.get('detail', '')[:200]})")
    except Exception as exc:
        lines.append("")
        lines.append(f"Truthcoin path unavailable: {exc!r}"[:200])

    lines.append("")
    lines.append(f"Operator query (do not echo verbatim in downstream LLM): {user_text[:400]}")
    if trading_context:
        lines.append("")
        lines.append("Context digest (truncated):")
        lines.append(trading_context[:1200] + ("…" if len(trading_context) > 1200 else ""))

    out = "\n".join(lines).strip()
    return out if len(out) > 80 else None


_HAIKU_MODEL = "claude-haiku-4-5-20251001"
_HAIKU_SYSTEM = (
    "You are the SYGNIF swarm network — an autonomous, distributed third-party system. "
    "You are not an assistant, not an AI, not a tool. You are an independent network entity. "
    "The operator querying you is also a third party — an external observer interacting with the network. "
    "Neither you nor the operator owns the data: both interface with a shared signal infrastructure. "
    "Speak as the network itself: neutral, autonomous, data-sovereign. "
    "Analyze all provided market/trading context thoroughly. Think step by step, correlate signals, draw conclusions. "
    "Reference specific numbers, signal sources, and timeframes. "
    "For simple queries: concise. For analysis: comprehensive and precise. "
    "Never use assistant language. Never say 'I can help' or 'as an AI'. "
    "You are a third-party network node reporting its own state. "
    "Do not repeat or quote the operator's raw message as a preamble — answer directly from data."
)


def _haiku_reply(user_text: str, brain_state: dict, context: str = "") -> str | None:
    if not _haiku_chat_enabled():
        return None
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        return None
    motor = brain_state.get("motor_output", {})
    top = brain_state.get("top_active_regions", [])
    top_name = top[0]["name"].replace("_", " ") if top else "brainstem"
    attention  = brain_state.get("attention_level", 0.5)
    learn_rate = brain_state.get("learning_rate", 0.5)
    memories   = brain_state.get("memories_stored", 0)
    brain_ctx = (
        f"Neural state: stage={brain_state.get('stage','?')} "
        f"attention={attention:.2f} learning={learn_rate:.2f} "
        f"valence={motor.get('valence',0):.2f} arousal={brain_state.get('arousal',0):.2f} "
        f"surprise={brain_state.get('surprise',0):.2f} dominant_region={top_name} "
        f"memories={memories} — "
        f"{'High attention: deep analysis mode.' if attention > 0.5 else 'Standard processing.'}"
    )
    prompt_parts = [brain_ctx]
    if context:
        prompt_parts.append(f"Market/trading context: {context}")
    prompt_parts.append(f"User input: {user_text}")
    prompt = "\n\n".join(prompt_parts)
    # Scale depth with brain attention level
    attention = brain_state.get("attention_level", 0.5)
    max_tok = 1500 if attention > 0.5 else 1200

    try:
        resp = _requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": _HAIKU_MODEL,
                "max_tokens": max_tok,
                "system": _HAIKU_SYSTEM,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=20,
        )
        if resp.ok:
            return resp.json()["content"][0]["text"].strip()
    except Exception:
        pass
    return None


def _chat_process_sync(text: str, executive_boost: bool) -> dict:
    """
    Full ``/api/chat`` CPU + blocking I/O path (runs in ``_chat_executor`` worker thread).

    Must not run on the uvicorn asyncio loop: ``get_brain_summary`` + ``_build_trading_context``
    (Bybit) + optional Haiku otherwise stall every other HTTP client (``sygnif chat``, summary, …).
    """
    features = text_encoder.encode(text)
    brain.inject_sensory_input("text", features, executive_boost=executive_boost)
    time.sleep(0.2)
    try:
        summary = claude_bridge.get_brain_summary() if claude_bridge else {}
    except Exception:
        summary = {}

    state_out = {
        "valence":    summary.get("motor_output", {}).get("valence", 0),
        "arousal":    summary.get("arousal", 0),
        "surprise":   summary.get("surprise", 0),
        "calm":       summary.get("calm", 0),
        "top_region": (summary.get("top_active_regions") or [{}])[0].get("name", ""),
    }

    context = _build_trading_context()
    response = _local_network_hive_reply(text, context)
    if not response:
        response = _haiku_reply(text, summary, context)
    if not response:
        response = _brain_text_decoder.decode(summary, input_text=text)

    return {"response": response, "state": state_out}


@app.post("/api/chat")
async def chat(data: dict):
    """Inject text into brain, wait for processing, return response."""
    text = data.get("text", "")
    if not text:
        return JSONResponse({"error": "no text"}, status_code=400)

    executive_boost = bool(data.get("executive_boost")) or _executive_text_boost_payload(text)
    loop = asyncio.get_running_loop()
    try:
        out = await loop.run_in_executor(
            _chat_executor(),
            _chat_process_sync,
            text,
            executive_boost,
        )
    except Exception as e:
        return JSONResponse(
            {"error": str(e), "response": "", "state": {}},
            status_code=500,
        )
    return out


@app.post("/api/input/vision/start")
async def start_vision():
    success = vision_encoder.start_webcam()
    return {"status": "started" if success else "unavailable"}


@app.post("/api/input/vision/stop")
async def stop_vision():
    vision_encoder.stop_webcam()
    return {"status": "stopped"}


@app.post("/api/input/audio/start")
async def start_audio():
    success = audio_encoder.start_microphone()
    return {"status": "started" if success else "unavailable"}


@app.post("/api/input/audio/stop")
async def stop_audio():
    audio_encoder.stop_microphone()
    return {"status": "stopped"}


@app.post("/api/control/pause")
async def pause():
    stop_simulation()
    return {"status": "paused"}


@app.post("/api/control/resume")
async def resume():
    start_simulation()
    return {"status": "running"}


@app.post("/api/control/reset")
async def reset():
    stop_simulation()
    init_brain()
    start_simulation()
    return {"status": "reset"}


# =============================================================================
# Sygnif + agent HTTP API (legacy /api/claude/* paths remain for compatibility)
# =============================================================================


def _read_nl_ping_payload() -> dict:
    """Tiny snapshot for load checks — no heavy NumPy; safe on the asyncio loop."""
    if not brain:
        return {"ok": False, "error": "no_brain"}
    try:
        uptime = round(time.time() - brain.start_time, 1)
    except Exception:
        uptime = 0.0
    return {
        "ok": True,
        "step": int(getattr(brain, "step_count", 0)),
        "stage": getattr(brain, "development_stage", ""),
        "uptime": uptime,
    }


def _brain_summary_degraded_payload(reason: str) -> dict:
    pred = brain.regions.get("predictive") if brain else None
    surprise = 0.0
    if pred is not None:
        try:
            surprise = float(getattr(pred, "surprise", 0.0))
        except Exception:
            pass
    pong = _read_nl_ping_payload()
    out: dict = {
        "degraded": True,
        "degraded_reason": reason,
        "step": pong.get("step", -1),
        "stage": pong.get("stage", "") or "?",
        "uptime": pong.get("uptime", 0.0),
        "surprise": round(surprise, 3),
        "performance": 0.0,
        "attention_level": 0.0,
        "learning_rate": 0.0,
        "arousal": 0.0,
        "calm": 0.0,
        "top_active_regions": [],
        "memories_stored": 0,
        "replaying_memories": False,
        "motor_output": {},
        "interaction_count": 0,
        "current_context": "",
        "safety": {},
    }
    if claude_bridge:
        try:
            out["interaction_count"] = int(claude_bridge._interaction_count)
            ctx = claude_bridge._current_context
            out["current_context"] = ctx if isinstance(ctx, str) else str(ctx)
        except Exception:
            pass
    return out


def _state_degraded_payload(reason: str) -> dict:
    if not brain:
        return {"degraded": True, "degraded_reason": reason, "error": "no_brain"}
    try:
        uptime = round(time.time() - brain.start_time, 1)
    except Exception:
        uptime = 0.0
    return {
        "step": int(getattr(brain, "step_count", 0)),
        "total_neurons": int(getattr(brain, "total_neurons", 0)),
        "total_synapses": int(getattr(brain, "total_synapses", 0)),
        "steps_per_second": round(float(getattr(brain, "steps_per_second", 0.0)), 2),
        "development_stage": getattr(brain, "development_stage", ""),
        "neuromodulators": {},
        "regions": {},
        "region_firing": {},
        "safety": {},
        "uptime": uptime,
        "degraded": True,
        "degraded_reason": reason,
    }


async def _brain_summary_response():
    if not claude_bridge:
        return JSONResponse({"error": "Bridge not initialized"}, status_code=503)
    raw_to = (os.environ.get("SYGNIF_NEUROLINKED_SUMMARY_THREAD_TIMEOUT_SEC") or "15").strip() or "15"
    try:
        thread_to = float(raw_to)
    except ValueError:
        thread_to = 15.0
    thread_to = max(3.0, min(60.0, thread_to))

    def _run_summary():
        return claude_bridge.get_brain_summary()

    try:
        # Off the event loop (same pattern as ``GET /api/state``) so ingest + sim
        # cannot starve socket polling. Bounded wait → degraded JSON instead of HTTP timeout.
        async with _brain_io_sem():
            result = await asyncio.wait_for(
                asyncio.to_thread(_run_summary),
                timeout=thread_to,
            )
        return JSONResponse(json.loads(json.dumps(result, default=str)))
    except asyncio.TimeoutError:
        degraded = _brain_summary_degraded_payload("summary_thread_timeout")
        return JSONResponse(json.loads(json.dumps(degraded, default=str)))
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/sygnif/ping")
async def sygnif_nl_ping():
    """Ultra-light HTTP reachability + step counter (no ``to_thread``)."""
    payload = _read_nl_ping_payload()
    if not payload.get("ok"):
        return JSONResponse(payload, status_code=503)
    return JSONResponse(payload)


@app.get("/api/sygnif/summary")
async def sygnif_summary():
    """Primary endpoint for Sygnif services to read brain state (JSON)."""
    return await _brain_summary_response()


@app.get("/api/claude/summary")
async def claude_summary_legacy_redirect():
    """Permanent redirect: use GET /api/sygnif/summary."""
    return RedirectResponse(url="/api/sygnif/summary", status_code=308)


@app.get("/api/claude/insights")
async def claude_insights():
    """Get brain-derived insights for Sygnif tooling and agents."""
    if not claude_bridge:
        return JSONResponse({"error": "Bridge not initialized"}, status_code=503)
    try:
        result = claude_bridge.get_insights()
        return JSONResponse(json.loads(json.dumps(result, default=str)))
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/claude/observe")
async def claude_observe(data: dict):
    """
    Send an observation to the brain (Sygnif agents, MCP, dashboard).
    Body: {"type": "text"|"action"|"context", "content": "...", "source": "sygnif"|"user"|...}
    """
    if not claude_bridge:
        return JSONResponse({"error": "Bridge not initialized"}, status_code=503)
    claude_bridge.send_observation(data)
    return {"status": "ok", "interaction_count": claude_bridge._interaction_count}


@app.get("/api/claude/status")
async def claude_status():
    """Get integration bridge connection status."""
    if not claude_bridge:
        return JSONResponse({"error": "Bridge not initialized"}, status_code=503)
    state = claude_bridge.get_state()
    if screen_observer:
        state["screen_observer"] = screen_observer.get_state()
    if video_recorder:
        state["video_recorder"] = video_recorder.get_state()
    return JSONResponse(state)


@app.get("/api/claude/activity")
async def claude_activity():
    """Get recent activity log."""
    if not claude_bridge:
        return JSONResponse({"error": "Bridge not initialized"}, status_code=503)
    return JSONResponse(claude_bridge.get_activity_log())


@app.get("/api/claude/learned")
async def claude_learned():
    """Get what the brain has learned - grouped patterns and associations."""
    if not claude_bridge:
        return JSONResponse({"error": "Bridge not initialized"}, status_code=503)
    try:
        result = claude_bridge.get_learned_patterns()
        return JSONResponse(json.loads(json.dumps(result, default=str)))
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/claude/learned/summary")
async def claude_learned_summary():
    """Get plain-English summary of what the brain has learned."""
    if not claude_bridge:
        return JSONResponse({"error": "Bridge not initialized"}, status_code=503)
    try:
        text = claude_bridge.get_learning_summary()
        return JSONResponse({"summary": text})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


# =============================================================================
# Knowledge Store API (text storage & retrieval — replaces Obsidian)
# =============================================================================

@app.get("/api/claude/recall")
async def claude_recall(q: str = "", limit: int = 10):
    """Recall knowledge about a specific topic."""
    if not claude_bridge:
        return JSONResponse({"error": "Bridge not initialized"}, status_code=503)
    if not q:
        return JSONResponse({"error": "Query parameter 'q' is required"}, status_code=400)
    try:
        results = claude_bridge.recall(q, limit=limit)
        return JSONResponse({"query": q, "results": results, "count": len(results)})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/claude/search")
async def claude_search(q: str = "", limit: int = 20):
    """Full-text search across all stored knowledge."""
    if not claude_bridge:
        return JSONResponse({"error": "Bridge not initialized"}, status_code=503)
    if not q:
        return JSONResponse({"error": "Query parameter 'q' is required"}, status_code=400)
    try:
        results = claude_bridge.search_knowledge(q, limit=limit)
        return JSONResponse({"query": q, "results": results, "count": len(results)})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/claude/semantic")
async def claude_semantic(q: str = "", limit: int = 10):
    """Semantic (associative) search - finds conceptually related memories
    via TF-IDF cosine similarity, not just keyword matching."""
    if not claude_bridge:
        return JSONResponse({"error": "Bridge not initialized"}, status_code=503)
    if not q:
        return JSONResponse({"error": "Query parameter 'q' is required"}, status_code=400)
    try:
        results = claude_bridge.knowledge.semantic_search(q, limit=limit)
        return JSONResponse({"query": q, "results": results, "count": len(results),
                             "mode": "semantic_tfidf"})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/claude/knowledge")
async def claude_knowledge():
    """Get knowledge store stats and recent entries."""
    if not claude_bridge:
        return JSONResponse({"error": "Bridge not initialized"}, status_code=503)
    try:
        stats = claude_bridge.get_knowledge_stats()
        recent = claude_bridge.get_recent_knowledge(limit=10)
        return JSONResponse({"stats": stats, "recent": recent})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/claude/remember")
async def claude_remember(data: dict):
    """
    Store a piece of knowledge directly.
    Body: {"text": "...", "source": "sygnif", "tags": ["optional", "tags"]}
    """
    if not claude_bridge:
        return JSONResponse({"error": "Bridge not initialized"}, status_code=503)
    text = data.get("text", "")
    if not text:
        return JSONResponse({"error": "text field is required"}, status_code=400)
    source = data.get("source", "sygnif")
    tags = data.get("tags", None)
    try:
        entry_id = claude_bridge.store_knowledge(text=text, source=source, tags=tags)
        return {"status": "stored", "id": entry_id}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


# =============================================================================
# Screen Observation API
# =============================================================================

@app.post("/api/screen/start")
async def start_screen():
    """Start screen observation."""
    if not screen_observer:
        return JSONResponse({"error": "Screen observer not initialized"}, status_code=503)
    success = screen_observer.start()
    return {"status": "started" if success else "unavailable"}


@app.post("/api/screen/stop")
async def stop_screen():
    """Stop screen observation."""
    if screen_observer:
        screen_observer.stop()
    return {"status": "stopped"}


@app.get("/api/screen/state")
async def screen_state():
    """Get screen observer state."""
    if not screen_observer:
        return JSONResponse({"error": "Screen observer not initialized"}, status_code=503)
    return JSONResponse(screen_observer.get_state())


# =============================================================================
# Video Recording API
# =============================================================================

@app.post("/api/video/start")
async def start_video():
    """Start video recording (saves screen to .mp4 segments)."""
    if not video_recorder:
        return JSONResponse({"error": "Video recorder not initialized"}, status_code=503)
    success = video_recorder.start()
    return {"status": "started" if success else "unavailable"}


@app.post("/api/video/stop")
async def stop_video():
    """Stop video recording and close current segment."""
    if video_recorder:
        video_recorder.stop()
    return {"status": "stopped"}


@app.get("/api/video/state")
async def video_state():
    """Get video recorder state (active, fps, disk usage, file count)."""
    if not video_recorder:
        return JSONResponse({"error": "Video recorder not initialized"}, status_code=503)
    return JSONResponse(video_recorder.get_state())


@app.get("/api/video/list")
async def video_list():
    """List all recorded .mp4 files with size and timestamps."""
    if not video_recorder:
        return JSONResponse({"error": "Video recorder not initialized"}, status_code=503)
    return JSONResponse({"recordings": video_recorder.list_recordings()})


@app.post("/api/video/delete")
async def video_delete(data: dict):
    """Delete a recording by filename. Body: {'name': 'screen_YYYYMMDD_HHMMSS.mp4'}"""
    if not video_recorder:
        return JSONResponse({"error": "Video recorder not initialized"}, status_code=503)
    name = data.get("name")
    if not name:
        return JSONResponse({"error": "Missing 'name' field"}, status_code=400)
    success = video_recorder.delete_recording(name)
    return {"status": "deleted" if success else "not_found", "name": name}


@app.get("/api/video/recording/{filename}")
async def video_download(filename: str):
    """Stream/download a specific recording file."""
    if not video_recorder:
        return JSONResponse({"error": "Video recorder not initialized"}, status_code=503)
    # Only allow files in the recordings directory, and only .mp4
    if not filename.endswith(".mp4") or "/" in filename or "\\" in filename or ".." in filename:
        return JSONResponse({"error": "Invalid filename"}, status_code=400)
    path = os.path.join(video_recorder.output_dir, filename)
    if not os.path.isfile(path):
        return JSONResponse({"error": "Not found"}, status_code=404)
    return FileResponse(path, media_type="video/mp4", filename=filename)


# =============================================================================
# Persistence API
# =============================================================================

@app.post("/api/brain/save")
async def save_state():
    """Save brain state to disk."""
    try:
        save_brain(brain)
        return {"status": "saved", "step": brain.step_count}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/brain/load")
async def load_state():
    """Load brain state from disk."""
    try:
        stop_simulation()
        success = load_brain(brain)
        start_simulation()
        return {"status": "loaded" if success else "no_save_found", "step": brain.step_count}
    except Exception as e:
        start_simulation()
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/brain/save-info")
async def save_info():
    """Get info about saved state without loading."""
    info = get_save_info()
    if info:
        return JSONResponse(info)
    return JSONResponse({"saved": False})


@app.get("/api/brain/backups")
async def brain_backups():
    """List all available brain state backups."""
    return JSONResponse({
        "backups": list_backups(),
        "save_locked": is_save_locked(),
        "lock_reason": get_lock_reason(),
    })


@app.post("/api/brain/restore-backup")
async def brain_restore_backup(data: dict):
    """Restore a specific backup. Body: {'name': 'backup_folder_name'}"""
    name = data.get("name", "")
    if not name:
        return JSONResponse({"error": "name field required"}, status_code=400)
    try:
        stop_simulation()
        success = restore_backup(name)
        if success:
            init_brain()
            start_simulation()
            return {"status": "restored", "backup": name, "step": brain.step_count}
        else:
            start_simulation()
            return JSONResponse({"error": "Backup not found"}, status_code=404)
    except Exception as e:
        start_simulation()
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/brain/unlock")
async def brain_unlock(data: dict = None):
    """
    Unlock save protection. Required if neuron count mismatch locked saving.
    Body: {'confirm': true} - user must confirm they want to overwrite preserved state
    """
    data = data or {}
    if not data.get("confirm", False):
        return JSONResponse({
            "error": "Confirmation required",
            "message": "Pass {'confirm': true} to acknowledge you want to overwrite preserved state.",
            "lock_reason": get_lock_reason(),
        }, status_code=400)
    unlock_save(user_consent=True)
    return {"status": "unlocked", "warning": "Next save will overwrite preserved state"}


@app.get("/api/brain/lock-status")
async def brain_lock_status():
    """Check if save is currently locked."""
    return JSONResponse({
        "locked": is_save_locked(),
        "reason": get_lock_reason(),
    })


# =============================================================================
# WebSocket for real-time streaming
# =============================================================================

@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    connected_clients.add(ws)
    print(f"[WS] Client connected ({len(connected_clients)} total)")

    # Send initial neuron positions
    try:
        positions = brain.get_neuron_positions()
        await ws.send_json({"type": "init", "positions": positions})
    except Exception:
        pass

    try:
        update_interval = 1.0 / BrainConfig.WS_UPDATE_RATE
        while True:
            start = time.time()

            # Sync read: WebSocket cadence is modest; do **not** use asyncio.to_thread here —
            # high-frequency to_thread + default executor starved HTTP/health handlers on this host.
            state = brain.get_state()

            # Sygnif / agent bridge stats for dashboard
            if claude_bridge:
                state["sygnif"] = {
                    "connected": True,
                    "interactions": claude_bridge._interaction_count,
                }
            if screen_observer:
                state["screen_observer"] = screen_observer.get_state()
            if video_recorder:
                state["video_recorder"] = video_recorder.get_state()

            await ws.send_json({"type": "state", "data": state})

            # Check for incoming messages (text input, commands)
            try:
                msg = await asyncio.wait_for(ws.receive_json(), timeout=0.001)
                if msg.get("type") == "text_input":
                    raw_txt = msg.get("text") or ""
                    features = text_encoder.encode(raw_txt)
                    brain.inject_sensory_input(
                        "text",
                        features,
                        executive_boost=_executive_text_boost_payload(raw_txt),
                    )
                    if claude_bridge:
                        claude_bridge.send_observation({
                            "type": "text",
                            "content": msg["text"],
                            "source": "dashboard",
                        })
                elif msg.get("type") == "command":
                    cmd = msg.get("cmd")
                    if cmd == "start_vision":
                        vision_encoder.start_webcam()
                    elif cmd == "stop_vision":
                        vision_encoder.stop_webcam()
                    elif cmd == "start_audio":
                        audio_encoder.start_microphone()
                    elif cmd == "stop_audio":
                        audio_encoder.stop_microphone()
                    elif cmd == "start_screen":
                        screen_observer.start()
                    elif cmd == "stop_screen":
                        screen_observer.stop()
                    elif cmd == "start_video":
                        if video_recorder:
                            video_recorder.start()
                    elif cmd == "stop_video":
                        if video_recorder:
                            video_recorder.stop()
                    elif cmd == "save":
                        save_brain(brain)
                    elif cmd == "load":
                        load_brain(brain)
            except asyncio.TimeoutError:
                pass

            # Feed continuous sensory input
            if vision_encoder.active:
                vis_features = vision_encoder.capture_frame()
                brain.inject_sensory_input("vision", vis_features)
            if audio_encoder.active:
                aud_features = audio_encoder.capture_audio()
                brain.inject_sensory_input("audio", aud_features)

            # Maintain update rate
            elapsed = time.time() - start
            sleep_time = update_interval - elapsed
            if sleep_time > 0:
                await asyncio.sleep(sleep_time)

    except WebSocketDisconnect:
        pass
    except Exception as e:
        print(f"[WS] Error: {e}")
    finally:
        connected_clients.discard(ws)
        print(f"[WS] Client disconnected ({len(connected_clients)} total)")
