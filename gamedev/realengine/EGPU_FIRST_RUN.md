# eGPU first run

Getting from "the enclosure is plugged in" to "an engine is running on it, and I
have proof it is".

An external GPU is not a plug-and-play upgrade. Every failure mode below is one
where the hardware is fine, the enclosure lights are on, Device Manager shows the
card, and the engine still runs on the wrong GPU or at a fraction of the speed it
should. Nothing errors. The frame rate is just bad, and the obvious next move —
optimising the scene — is wasted effort.

---

## Step 1 — Preflight

On the machine with the eGPU attached:

```bash
python3 gamedev/realengine/tools/egpu_preflight.py
```

Read-only: it changes no settings, installs nothing, writes no files. Anything it
cannot determine is reported `UNKNOWN` rather than guessed.

```
  PASS    GPU enumeration
  PASS    Thunderbolt enrolment
  PASS    PCIe link width
  WARN    Display attachment
  ...
```

`--json` for machine-readable output. Exit code is 0 for PASS/WARN, 1 otherwise.

---

## Step 2 — The four traps, in the order they cost time

### 2.1 The application runs on the integrated GPU

**The most common one by a distance.** Windows picks a GPU per application, and
its default heuristic frequently picks the iGPU for an editor process. The eGPU
sits at 2% utilisation while you wonder why a cube renders at 40 fps.

Fix: **Settings → System → Display → Graphics** → Add desktop app → set **High
performance**.

Add each executable **separately** — the setting is per binary:

- `Unity.exe` (the editor itself, under `Editor\<version>\Editor\`)
- `UnityHub.exe`
- `UnrealEditor.exe`
- your built player

Launching the editor from the Hub does **not** inherit the Hub's preference.

NVIDIA users can also set this in NVIDIA Control Panel → Manage 3D settings →
Program Settings, which is per-profile and survives Unity version upgrades better.

### 2.2 The display is attached to the laptop's internal panel

If the eGPU renders but the picture appears on the built-in screen, every finished
frame is copied back across Thunderbolt, against the direction of the bus.
Typically **20–50% of the frame rate**, silently.

Fix: plug a monitor into the enclosure. This is the single largest eGPU
performance factor and costs nothing.

If you must use the internal panel, expect the hit and do not benchmark on it —
you will be measuring Thunderbolt, not the GPU.

### 2.3 The PCIe link is not x4

Thunderbolt 3/4 gives PCIe 3.0 **x4**, roughly 32 Gbit/s, versus x16 for a
desktop slot. That is normal and expected; an eGPU is meaningfully slower than
the same card in a desktop, especially at low resolutions where the CPU-GPU
traffic dominates.

What is *not* normal is negotiating **x1**, or dropping to PCIe 1.1. That is an
order of magnitude worse and produces no error anywhere. Causes, in order of
likelihood:

- A non-Thunderbolt cable. USB-C charging cables fit perfectly and negotiate USB
  speeds. Use the cable that came with the enclosure.
- A dock or hub in the chain. Connect the enclosure directly to the laptop.
- A BIOS setting: Thunderbolt security level, or PCIe tunnelling disabled.

The preflight reports this via `nvidia-smi`; on AMD, check `lspci -vv` for
`LnkSta:` and compare against `LnkCap:`.

### 2.4 Resizable BAR / above-4G decoding

Usually a BIOS default and worth a few percent on modern cards. Enable **Above 4G
Decoding** and **Re-Size BAR Support** if the firmware offers them.

---

## Step 3 — Install the engine

**Recommendation: Unity 2022.3 LTS**, for two reasons specific to your situation.
The platformer work in `gamedev/` is 2D, where Unity is clearly ahead — Unreal's
Paper2D is a neglected corner. And on a Thunderbolt-limited machine, Unity's much
lighter footprint matters: ~30 GB against Unreal's ~120 GB from the launcher, and
minutes rather than tens of minutes for a first build.

Pick Unreal instead if the goal is 3D rendering quality or networked multiplayer.
`LEARNING_PATH.md` has the full comparison.

Install order that avoids rework:

1. Unity Hub, then **2022.3 LTS** (not the tech stream — packages and platform
   modules lag behind it for no benefit).
2. Modules: **Linux Build Support (IL2CPP)** if you want headless CI builds;
   Windows Build Support is included.
3. Open `gamedev/realengine/unity/` as an existing project.
4. Run `gamedev/realengine/unity/sync-core.sh` first, or the core scripts will be
   missing and the project will not compile.

---

## Step 4 — Prove the eGPU is actually being used

Do not skip this. "It is installed" and "it is being used" are different claims
and only one of them matters.

**In Unity**, three independent checks:

1. `Help → About Unity` names the graphics device.
2. Attach `GpuDiagnostics` (in `Assets/Scripts/Runtime/`) to a bootstrap object.
   It logs the adapter, vendor, VRAM and API on startup, and **warns loudly if
   the name looks integrated**.
3. Task Manager → Performance: play the scene and watch which GPU's utilisation
   moves. This is the one that cannot be argued with.

**In Unreal**: the log line `Using D3D12 adapter`, or `stat unit` in-game.

Expected `GpuDiagnostics` output on a working setup:

```
[GPU] NVIDIA GeForce RTX 3070  |  vendor: NVIDIA  |  VRAM: 8192 MB  |  API: Direct3D12
```

If instead you see:

```
[GPU] Intel(R) Iris(R) Xe Graphics  |  ...
[GPU] Rendering on what looks like an INTEGRATED GPU ...
```

go back to step 2.1. Nothing else is worth doing until that line is right.

---

## Step 5 — A first measurement worth having

Before building anything, get a number you can compare against later.

1. New scene, one directional light, a few hundred rotating cubes.
2. `Application.targetFrameRate = -1;` and `QualitySettings.vSyncCount = 0;` —
   otherwise you will measure your monitor's refresh rate rather than the GPU.
3. Record frame time (not FPS — frame time is linear and comparable) in the
   Profiler, on the eGPU and again with the app forced to the iGPU.

The gap between those two numbers is what the eGPU is buying you. If it is small,
something in step 2 is still wrong — that is the whole point of measuring it now
rather than after a month of work.

Then run the existing suite to confirm the toolchain still passes on that machine:

```bash
gamedev/realengine/verify.sh
```

---

## Note on the WSL path

If you work in WSL: WSL2 supports GPU compute via `/dev/dxg`, and OpenGL/Vulkan
through the D3D12 mapping layer, but it is **not** a good target for running a
game engine editor. Run Unity and Unreal natively on Windows and keep WSL for the
toolchain — the `dotnet`, `cmake` and Python parts of this repository all run fine
there.

The preflight detects WSL and probes the Windows GPU list through PowerShell, so
it gives a useful answer from either side.
