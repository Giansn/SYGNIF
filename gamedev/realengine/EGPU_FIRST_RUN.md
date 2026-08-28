# eGPU first run (Linux)

Getting from "the enclosure is plugged in" to "an engine is running on it, and I
have proof it is".

Target machine: **ThinkX1, Linux**. eGPU on Linux is a harder story than on
Windows — there is no per-application GPU picker, hotplug is unreliable, and the
single most common outcome is that everything appears to work while the eGPU is
either not authorised, not driving anything, or running on the wrong driver.

The Windows-specific version of these notes is at the end.

---

## Step 1 — Preflight

```bash
cd ~/sygnif                    # wherever your SYGNIF clone lives
git fetch origin
git checkout claude/game-development-learning-zxadip
python3 gamedev/realengine/tools/egpu_preflight.py
```

Read-only: changes no settings, installs nothing, writes no files. Exit 0 on
PASS/WARN, 1 otherwise. `--json` for machine-readable output.

**Run it from a terminal inside your desktop session, not over SSH.** Several
checks read `XDG_SESSION_TYPE` and the DRM connector state, and from an SSH shell
they describe nothing useful.

---

## Step 2 — The Linux blockers, in the order they bite

### 2.1 Thunderbolt authorisation

Linux does not trust a Thunderbolt device by default. Until it is enrolled, the
GPU does not appear in `lspci` at all — the enclosure is powered, the fan spins,
and the machine acts as though nothing is attached.

```bash
boltctl list                       # is it there, and is it authorised?
boltctl enroll <uuid>              # trust it permanently
```

If `boltctl` is missing: `sudo apt install bolt`.

Some firmware also has a BIOS-level Thunderbolt security setting; "User
Authorization" is the one that works with `boltctl`.

### 2.2 The kernel driver — the blocker with no Windows equivalent

```bash
lspci -k | grep -A3 -iE 'vga|3d controller'
```

Look at `Kernel driver in use:`. If it says **`nouveau`** on an NVIDIA card, stop
here. Nouveau will light up a desktop perfectly well, so everything looks fine,
but it has no usable Vulkan for modern cards and cannot reclock the GPU — it runs
at a fraction of its clocks. An engine on nouveau is not slow because of
Thunderbolt; it is slow because the card is idling.

Fix: install the proprietary driver and blacklist nouveau.

```bash
sudo apt install nvidia-driver nvidia-vulkan-icd     # Debian
# then reboot, and confirm:
nvidia-smi
```

`nvidia-smi` failing after a kernel update usually means DKMS did not rebuild the
module. `dmesg | grep -i nvidia` will say so.

### 2.3 Nothing selects the eGPU for you

This is the big structural difference from Windows. There is no
"Settings → Graphics → High performance". An application launched normally
renders on the **integrated** GPU, full stop.

You launch through PRIME offload instead:

```bash
# NVIDIA
__NV_PRIME_RENDER_OFFLOAD=1 __GLX_VENDOR_LIBRARY_NAME=nvidia <app>

# AMD / Intel discrete
DRI_PRIME=1 <app>
```

Verify the mechanism itself before trusting it with an engine:

```bash
__NV_PRIME_RENDER_OFFLOAD=1 __GLX_VENDOR_LIBRARY_NAME=nvidia glxinfo | grep "OpenGL renderer"
```

That must name the eGPU. If it names the Intel iGPU, offload is not working and
no amount of engine configuration will help.

**For Unity specifically:** put the variables in the `Exec=` line of the Unity Hub
`.desktop` file. The Hub launches the editor as a child process, so the editor
inherits the Hub's environment — setting it only for the editor binary is the
thing people try first and it does not stick.

### 2.4 Which display the monitor is plugged into

If the eGPU renders but the picture appears on the laptop's internal panel, every
finished frame is copied back across Thunderbolt, against the direction of the
bus. Typically **20–50% of the frame rate**, silently.

```bash
# which cards have a connected output
for c in /sys/class/drm/card*-*/status; do
  printf '%s ' "$c"; cat "$c"
done
```

A monitor plugged into the enclosure removes the cost entirely. This is the
cheapest performance you will ever buy. The preflight derives this automatically
on Linux from the same DRM data.

### 2.5 Hotplug, and why you should just attach it at boot

X11 will not add a GPU to a running server, so an eGPU attached after login is
typically invisible until you log out and back in. Wayland is better with recent
NVIDIA drivers (555+) but still inconsistent across compositors.

Save yourself the debugging: **attach the enclosure before powering on.**

### 2.6 PCIe link width

```bash
nvidia-smi --query-gpu=name,pcie.link.gen.current,pcie.link.width.current,pcie.link.width.max --format=csv
```

`pcie.link.width.current` should be **4**. Thunderbolt 3/4 gives PCIe 3.0 x4,
roughly 32 Gbit/s, versus x16 for a desktop slot — that is normal and expected,
and an eGPU is genuinely slower than the same card in a tower.

**x1 is not normal.** It is an order of magnitude worse, with no error anywhere.
Causes, in order of likelihood: a USB-C charging cable rather than a
Thunderbolt-certified one (they fit perfectly and negotiate USB speeds), a dock
or hub in the chain, or a BIOS Thunderbolt setting.

On AMD, `lspci -vv` and compare `LnkSta:` against `LnkCap:`.

---

## Step 3 — Install the engine

**Unity 2022.3 LTS**, and on Linux the case is stronger than it was on Windows:

- The platformer work in `gamedev/` is 2D, where Unity is clearly ahead —
  Unreal's Paper2D is a neglected corner.
- **Unreal has no binary Linux release.** Epic ships Linux as a source zip, so
  getting an editor means a full engine build: 100 GB+ and hours, versus minutes
  for a Unity install. That is a substantial difference in how quickly you reach
  a first frame.
- Unity's footprint is ~30 GB against Unreal's 100 GB+, which matters given the
  preflight's disk check.

```bash
# Unity Hub, Debian/Ubuntu
wget -qO- https://hub.unity3d.com/linux/keys/public | gpg --dearmor | sudo tee /usr/share/keyrings/Unity_Technologies_ApS.gpg > /dev/null
sudo sh -c 'echo "deb [signed-by=/usr/share/keyrings/Unity_Technologies_ApS.gpg] https://hub.unity3d.com/linux/repos/deb stable main" > /etc/apt/sources.list.d/unityhub.list'
sudo apt update && sudo apt install unityhub
```

Then install **2022.3 LTS** from the Hub — not the tech stream, whose packages
and platform modules lag for no benefit.

Before opening the project:

```bash
gamedev/realengine/unity/sync-core.sh
```

Without this the core scripts are missing and the project will not compile.

Open `gamedev/realengine/unity/` as an existing project.

---

## Step 4 — Prove the eGPU is actually being used

Do not skip this. "It is installed" and "it is being used" are different claims
and only one of them matters. On Linux, where nothing selects the GPU for you,
the default outcome is the wrong one.

Three independent checks:

1. `Help → About Unity` names the graphics device.
2. Attach `GpuDiagnostics` (in `Assets/Scripts/Runtime/`) to a bootstrap object.
   It logs adapter, vendor, VRAM and API at startup, and **warns loudly if the
   name looks integrated**.
3. `nvidia-smi dmon` in a terminal while the scene plays — watch whether the
   eGPU's utilisation actually moves. This is the check that cannot be argued
   with.

Working:

```
[GPU] NVIDIA GeForce RTX 3070  |  vendor: NVIDIA  |  VRAM: 8192 MB  |  API: Vulkan
```

Not working:

```
[GPU] Intel(R) Iris(R) Xe Graphics  |  ...
[GPU] Rendering on what looks like an INTEGRATED GPU ...
```

Back to step 2.3. Nothing else is worth doing until that line is right.

---

## Step 5 — A first measurement worth having

Get a number now, so later changes have something to compare against.

1. New scene, one directional light, a few hundred rotating cubes.
2. `Application.targetFrameRate = -1;` and `QualitySettings.vSyncCount = 0;` —
   otherwise you measure your monitor's refresh rate, not the GPU.
3. Record **frame time**, not FPS (frame time is linear and comparable), in the
   Profiler. Once with PRIME offload, once without.

The gap between those two numbers is what the eGPU is buying you. If it is
small, something in step 2 is still wrong — which is exactly why you measure
before building anything, not after a month of work.

Then confirm the toolchain still passes on that machine:

```bash
gamedev/realengine/verify.sh
```

---

## Appendix — Windows / WSL

Kept because the machine mix may change.

- **Per-application GPU:** Settings → System → Display → Graphics → add the
  executable → High performance. It is **per binary**, so `Unity.exe`,
  `UnityHub.exe` and `UnrealEditor.exe` each need adding; launching the editor
  from the Hub does not inherit the Hub's preference.
- **Resizable BAR / Above 4G Decoding:** enable in BIOS if offered.
- **Thunderbolt:** Thunderbolt Control Center → "Always Connect".
- **WSL:** supports GPU compute via `/dev/dxg`, but is a poor target for running
  an engine editor. Run the engine natively; keep WSL for the toolchain. The
  preflight detects WSL and queries the Windows GPU list through PowerShell, and
  looks for `nvidia-smi.exe` rather than `nvidia-smi` — under WSL the Linux one,
  where it exists at all, describes the virtualised WSL GPU rather than the card
  in the enclosure.
