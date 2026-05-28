# Legion T5 — GPU capability plan for the new SYGNIF center

> **2026-05-28 18:38 UTC** · Scoping doc for the new center box. What the
> RTX 5060 + Arrow Lake NPU actually unlock for SYGNIF, with VRAM-aware model
> picks and a concrete first step. Not a spec change — the canonical service
> inventory and language plan live in `SYGNIF.md` (§6/§7). This is a hardware
> capability scope for the box that replaces the X1 Yoga as brain/author.

## 0. TL;DR

The headline is **not** the CPU — it's that the center box now has a discrete
NVIDIA GPU for the first time. The X1 Yoga only ever had the **13-TOPS Intel
NPU**, which is why `trade_overseer/npu_genai_client.py` caps generation at 256
tokens with a **120-second timeout**: a 7B int4 model on that NPU is slow. The
RTX 5060 collapses that to a few seconds and lets a better model fit.

| Capability | GPU value | Verdict |
|---|---|---|
| **Local-LLM commentary rephraser** (`trade_overseer`, §6.4) | ★★★ High | **Do first.** Path already scaffolded; GPU makes it fast + better. |
| Transformer embeddings (upgrade from Model2Vec, §6.1) | ★★ Moderate | Optional, low urgency. Model2Vec stays on CPU (correct as designed). |
| ML ensemble (RF/XGB/LogReg predict_loop) | ★★ Moderate | Live = no benefit. **Backtest/retrain sweeps = big win** (RAPIDS, WSL2). |
| NeuroLinked brain on GPU (3k-neuron SNN) | ★ Low *now* | **Defer.** Enabler for scaling to 30k–100k neurons, not a free speedup. |

The binding constraint everywhere is **8 GB of VRAM**. Good news: the whole
commentary + embeddings layer fits in it with room to spare (see §2).

## 1. The hardware (verified 2026-05-28)

**Lenovo Legion T5** — 1 TB storage, 32 GB RAM.

| Component | Spec | Relevance |
|---|---|---|
| **GPU** | GeForce RTX 5060 (desktop), GB206 Blackwell, **8 GB GDDR7**, 128-bit (~450 GB/s class), 3840 CUDA, 120 5th-gen Tensor cores (FP4-capable) | The new fast local-inference path. LLM decode is bandwidth-bound → ~450 GB/s is the number that matters. |
| **CPU** | Intel Core Ultra 5 225 (Arrow Lake-S), 10C (6P+4E)/10T, up to 4.9 GHz, 20 MB cache | Solid host for the Linux services + brain at current scale. |
| **NPU** | Intel AI Boost, **13 TOPS** (23 TOPS peak INT8 incl. CPU) | Same class as the existing OpenVINO path. Keep as a low-power, GPU-free fallback. |
| **iGPU** | Intel Xe (Arrow Lake) | Display + minor offload; not a compute target here. |
| RAM | 32 GB | Comfortable for brain + MCP + swarm + an Ollama model paged in RAM. |

Frameworks the NPU/CPU already speak (per Intel): OpenVINO, ONNX RT, DirectML,
WindowsML, WebNN. The GPU adds CUDA / cuDNN / TensorRT / GGUF-CUDA.

### Roofline sanity check (estimates, not benchmarks)

LLM token generation is memory-bandwidth-bound: `tok/s ≈ bandwidth ÷ active
weight bytes`. At ~450 GB/s:

| Model (Q4_K_M) | ~Resident VRAM | ~Realistic decode | 200-tok commentary |
|---|---|---|---|
| 3B (e.g. the staged Plutus-3B) | ≈ 2.5 GB | ~80–130 tok/s | **~1.5–2.5 s** |
| 7–8B (Qwen2.5-7B / Llama-3.1-8B) | ≈ 5–5.5 GB | ~40–70 tok/s | **~3–5 s** |

Versus the **13-TOPS NPU** on the same 7B int4: tens of seconds to ~2 min
(hence the code's 120 s timeout). **That latency cliff is the whole point of
the GPU.**

## 2. VRAM budget — everything local coexists in 8 GB

The fear with an 8 GB card is "nothing fits." For SYGNIF's actual workloads it
fits comfortably, because commentary is the only heavyweight and it's small:

```
Commentary LLM   3B Q4   ≈ 2.5 GB    │ or 7–8B Q4 ≈ 5.0–5.5 GB
Embeddings       bge-base ≈ 0.4 GB   │ (optional; Model2Vec is CPU, ~0 GB)
Brain (3k SNN)   ≈ 0.05 GB           │ (negligible — but defer, see §6)
Headroom / KV    ≈ 1.0–1.5 GB        │ ctx is small (Modelfile num_ctx 2048)
─────────────────────────────────────
Always-resident target:  ~3 GB (3B) … ~6 GB (7–8B)   → fits, with margin
```

What does **not** fit: any fp16 7B (~14 GB), a 13B+ at Q4 (~8–9 GB leaves no
room), or running a big LLM **and** a scaled-up brain simultaneously. Stay at
≤8B Q4 for commentary and the budget is never tight.

Ollama's keep-alive unloads the model after idle, so even a 7–8B only occupies
VRAM around commentary events (which are bursty: trade open/close, regime
change, STRONG_* signals — dozens/day, latency-tolerant).

## 3. ★ Priority 1 — GPU commentary for `trade_overseer`

### What already exists (don't rebuild it)

- **`trade_overseer/llm_client.py`** — pluggable backend, priority order:
  1. `OVERSEER_AGENT_URL` (localhost POST `{prompt, source}` → `{commentary|text}`; `/health` probe; `SYGNIF_LOCAL_AGENT_ONLY=1` blocks non-local URLs)
  2. `SYGNIF_LLM_BACKEND=npu` → OpenVINO GenAI on the NPU
  3. `ANTHROPIC_API_KEY` → Claude Haiku (legacy cloud)
  4. rules-only fallback
- **`trade_overseer/npu_genai_client.py`** — OpenVINO `LLMPipeline(model, "NPU", …)`, models under `%USERPROFILE%\npu_models\` (qwen2.5-1.5b / TinyLlama-1.1b / deepseek-r1-qwen-7b, all int4).
- **`trade_overseer/models/Modelfile`** — a staged **Ollama** model, `plutus-3b-q4.gguf`, Llama-3 chat template, tuned trade-analyst SYSTEM prompt, `temperature 0.4 / num_predict 200 / num_ctx 2048`.
- A strong, format-constrained `SYSTEM_PROMPT` in `llm_client.py` (the `EDGE[f] +3.4% … TRAIL` contract).

So the Ollama/GGUF path is *already authored* — it just never had a GPU to run
fast on. The Legion is that GPU.

### Recommended setup

1. **Serve with Ollama** (CUDA build). Native Windows-NVIDIA is the simplest;
   WSL2 also works. It's OpenAI-compatible and matches the existing `Modelfile`.
   - `ollama create plutus -f trade_overseer/models/Modelfile` (fix the `FROM`
     path to where the GGUF lives on the Legion).
2. **Model picks:**
   - *Fast / already-built:* **Plutus-3B-Q4** (~2.5 GB, ~2 s/commentary).
   - *Quality upgrade:* **Qwen2.5-7B-Instruct Q4_K_M** (~5 GB) or
     **Llama-3.1-8B-Instruct Q4_K_M** (~5.5 GB) — both fit, ~3–5 s/commentary.
3. **Wire it in.** Two options, smallest first:
   - **(a) Zero-code shim:** a ~30-line localhost service that adapts Ollama
     `/api/generate` → the overseer's `{prompt,source}`→`{commentary}` contract,
     then set `OVERSEER_AGENT_URL=http://host.docker.internal:<port>/overseer/commentary`.
     Reuses the existing #1 path untouched.
   - **(b) Native backend branch (cleaner):** add `SYGNIF_LLM_BACKEND=ollama`
     to `llm_client.py`, mirroring the `npu` branch. Sketch:

     ```python
     _LLM_BACKENDS_OLLAMA = frozenset({"ollama", "cuda", "gpu"})
     # … in evaluate(), after the npu branch:
     if _llm_backend() in _LLM_BACKENDS_OLLAMA:
         base = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434")
         model = os.environ.get("OLLAMA_MODEL", "plutus")
         r = requests.post(f"{base}/api/generate", timeout=timeout, json={
             "model": model, "system": SYSTEM_PROMPT, "prompt": prompt,
             "stream": False, "options": {"temperature": 0.4, "num_predict": 256},
         })
         if r.ok:
             out = (r.json().get("response") or "").strip()
             if out:
                 return _validate_numeric_grounding(out, prompt) or None
     ```

4. **Fallback chain stays intact:** GPU primary → NPU (`npu`) if the GPU host is
   down → rules-only. Keep Anthropic disabled (`SYGNIF_LOCAL_AGENT_ONLY=1` is
   already the default), consistent with the move off the cloud path.

### ★ Mandatory guardrail — numeric grounding (doctrine Rule 1)

> *Real data only. Never fabricate prices, indicators, equity, or P&L.*

An LLM narrator is a hallucination surface for **financial figures**. Per §6.4,
the LLM only *rephrases* — the brain/templates own *what* is true. Enforce it:

- Keep temperature low (Modelfile is already 0.4).
- **Post-validate every generation:** regex-extract all `$`/`%`/price tokens
  from the output; assert each appears in the input `prompt`. On any unmatched
  number → discard the LLM output and fall back to the rules-only summary.
- This `_validate_numeric_grounding(out, prompt)` helper (referenced in the
  sketch above) is the single most important addition — ship it with the
  backend, not after.

## 4. Priority 2 — embeddings (only if recall quality limits commentary)

**Model2Vec (potion-base-8M) does not need the GPU.** It's a static
token→vector lookup (~30 MB, CPU, sub-millisecond) — that's its design point.
Leave it on CPU as the §6.1 plan specifies.

The GPU only matters if you later want **higher-quality semantic recall** from
`knowledge.db` (the §6.2 nearest-neighbor retrieval that grounds commentary).
Then a transformer encoder helps:

- **BAAI/bge-base-en-v1.5** (768-d, ≈ 0.4 GB) — fast, strong, the default pick.
- **bge-large-en-v1.5** (1024-d, ≈ 1.0–1.3 GB) — if recall quality justifies it.
- GPU shines for **bulk re-embedding** the 50k+ existing `knowledge.db` entries.

Verdict: low urgency. Don't spend effort here until retrieval quality is the
thing holding commentary back.

## 5. Priority 2 — ML ensemble (research loop, not live)

The `predict_loop` ensemble (RF + XGB + LogReg) runs every 5 min — it is **not
compute-bound**, so live inference gets nothing from the GPU.

Where the GPU pays off is **research velocity**: backtests, walk-forward
validation, and hyperparameter sweeps over years of OHLCV.

- **XGBoost**: native GPU (`device="cuda"`, `tree_method="hist"`).
- **RandomForest / LogReg**: **RAPIDS cuML** drop-in replacements.
- Effect: hour-long sweeps → minutes.

**Caveat — WSL2.** RAPIDS is Linux-first; on this box run it under **WSL2
(Ubuntu)** with GPU passthrough (mature on NVIDIA). Native-Windows RAPIDS is
limited. Keep live `predict_loop` on CPU; use the GPU for the offline
retrain/backtest loop.

## 6. Deferred — NeuroLinked brain on GPU

A 3000-neuron Izhikevich net is **tiny** (≈ 9M synapses ≈ 36 MB f32). At this
scale:

- It already runs faster than real-time on CPU.
- A naive per-step GPU port can be **slower** than CPU due to kernel-launch
  overhead — there isn't enough parallelism per step to amortize it.

So the GPU is **not** a free speedup for the brain as it stands. It becomes
worthwhile as the **enabler for scaling** — the §4 planned 3000→4000 language
region, and beyond toward 30k–100k neurons, where a vectorized CuPy/PyTorch or
Brian2CUDA/GeNN implementation clearly wins.

Note for later: at 100k neurons with denser synapses, the brain's VRAM could
rival the LLM's — at that point the center box has to choose or time-share.
Today, leave the brain on CPU (it lives on EC2 anyway); revisit when you
consolidate it onto the Legion *and* scale neuron count.

## 7. Cross-cutting — OS / runtime layout

The Legion ships Windows; the X1 was "Windows + WSL". The clean split:

| Runtime | Hosts | Why |
|---|---|---|
| **WSL2 (Ubuntu)** | Linux services (systemd parity with EC2), RAPIDS, brain | Matches the EC2 service model; required for RAPIDS; mature CUDA passthrough. |
| **Ollama (CUDA)** | commentary LLM | Runs native-Windows *or* WSL2; OpenAI-compatible; easiest GPU path. |
| **OpenVINO (NPU)** | fallback commentary | Windows-native; the existing `npu_genai_client.py` path, GPU-free. |

This is a "later" decision — it doesn't block Priority 1, which only needs
Ollama + the GPU.

## 8. Recommended sequence

1. **Install Ollama + CUDA** on the Legion; `ollama create plutus` from the
   existing `Modelfile` (fix the GGUF path). Confirm tok/s.
2. **Wire `trade_overseer`**: add the `ollama` backend branch (§3 option b) +
   the `_validate_numeric_grounding` guardrail. Keep NPU + rules as fallback.
3. **Benchmark vs NPU** on real briefing prompts (latency + quality). Promote
   GPU to primary; NPU stays as the GPU-down fallback.
4. *(optional)* Upgrade commentary model to **7–8B Q4** if quality warrants.
5. *(optional)* **RAPIDS + XGBoost-GPU** under WSL2 for the backtest/retrain loop.
6. *(deferred)* Transformer embeddings (§4); GPU brain at scale (§6).

Step 1–2 is the whole high-value payoff and touches one file
(`trade_overseer/llm_client.py`) plus the `Modelfile` path. Everything else is
opportunistic.

---

*Capability scope only. Service inventory, trading doctrine, and the language
plan remain canonical in `SYGNIF.md`. Edit there (not here) for spec changes.*
