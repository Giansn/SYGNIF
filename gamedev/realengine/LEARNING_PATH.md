# Learning path: Unity and Unreal

A milestone curriculum through the subsystems a real engine owns. Ordered so
each step is verifiable before the next depends on it.

Milestones marked **[done]** are complete and checked by `verify.sh` in this
repository. Everything else needs an engine install and is written as what to
build and what to check, not as prose to read.

The bias throughout: **prefer a milestone that ends in something you can
observe.** "Read about the animation system" is not a milestone. "The character
plays a run cycle that stops within one frame of the input releasing" is.

---

## Stage 0 — Engine-free foundations **[done]**

| # | Milestone | Verified by |
|---|---|---|
| 0.1 | Deterministic RNG with identical streams across languages | 3 test suites asserting shared reference values |
| 0.2 | Fixed-timestep accumulator, drift-free and spiral-proof | tick-count tests over 36 000 frames |
| 0.3 | Swept AABB, move-and-slide | brute-force cross-check, tunnelling regressions |
| 0.4 | Tile grid with grid broadphase | inspected-tile-count assertions |
| 0.5 | Input edge detection surviving multi-tick frames | latching tests |
| 0.6 | Adapters that compile against both engines' APIs | shim builds |

**Why this is stage 0 and not stage 3.** Every one of these is something both
engines either lack or provide in a form that cannot be unit-tested. Getting
them right first means the rest of the learning happens on top of a foundation
that is already trustworthy.

---

## Stage 1 — Get the engine running and the project structured

### Unity

| # | Milestone | How to check it worked |
|---|---|---|
| 1.1 | Install Unity 2022.3 LTS via Unity Hub; open `unity/` | Editor opens with no console errors |
| 1.2 | Run `sync-core.sh`, confirm `GameCore.asmdef` compiles | Core scripts compile; adding `using UnityEngine;` to a core file **fails**, proving `noEngineReferences` is enforced |
| 1.3 | Port the C# tests to the Test Runner (EditMode) | 68 tests green in the Test Runner window |
| 1.4 | Build a headless player from the command line | `-batchmode -quit -buildLinux64Player` exits 0 |

**Trap:** LTS versus latest. Use LTS. Packages, render pipelines and platform
modules lag, and a tech-stream version will cost you days to no benefit.

### Unreal

| # | Milestone | How to check it worked |
|---|---|---|
| 1.5 | Install UE 5.4 (Epic Launcher, or source for engine debugging) | Editor launches |
| 1.6 | Generate project files for `SygnifGame.uproject`, build the module | Module compiles; components appear in the Add Component list |
| 1.7 | Move the core into its own UBT module rather than include paths | Core module builds without `Engine` or `CoreUObject` deps |
| 1.8 | Wrap the C++ tests as Automation tests | `UnrealEditor-Cmd -ExecCmds="Automation RunTests Sygnif" -unattended` passes |

**Trap:** the first build is 30–60 minutes and several hundred GB of disk across
engine plus DDC. Budget for it; it is not broken.

---

## Stage 2 — The loop, input and movement, on real hardware

| # | Milestone | Check |
|---|---|---|
| 2.1 | Character moves via the core mover, not engine physics | Same input produces the same path twice from the same start state |
| 2.2 | Input latching under variable frame rate | Cap to 20 fps and 200 fps; a single jump tap produces exactly one jump at both |
| 2.3 | Render interpolation using the accumulator's alpha | 60 Hz simulation at 144 Hz displays smoothly, not in 2–3 frame steps |
| 2.4 | Deliberately break it to see the failure | Read `GetKeyDown` in `FixedUpdate` and reproduce dropped and doubled jumps |

Milestone 2.4 is not filler. The failure modes described in `PORT_PLAN.md` §3.2
are worth *seeing* once, because they are intermittent and frame-rate dependent,
and recognising them later saves days.

---

## Stage 3 — What the engine actually gives you

This is the part the from-scratch work deliberately did not cover, and the part
that is most of the value of using an engine at all.

### 3A. Rendering and assets

| # | Milestone | Check |
|---|---|---|
| 3.1 | Sprites/flipbooks replacing the software renderer | Character animates; the hand-written rasteriser is deleted |
| 3.2 | Draw-call batching / instancing | Frame Debugger (Unity) or `stat RHI` (Unreal) shows batching working |
| 3.3 | Asset loading: Addressables (Unity) / Asset Registry + soft references (Unreal) | Level assets load on demand; memory measurably drops |
| 3.4 | Import pipeline: texture compression, sprite atlases | Build size falls; atlas is used, verified in the profiler |

**Trap:** hard references. A `UPROPERTY` hard reference to an asset pulls its
entire dependency chain into memory on load. In Unity the equivalent is a direct
prefab reference. Soft references and Addressables exist for this and it is the
usual cause of a load time nobody can explain.

### 3B. Physics, when you actually want it

| # | Milestone | Check |
|---|---|---|
| 3.5 | Rigid bodies for debris and props | Objects fall and settle |
| 3.6 | Set collision detection to continuous, then fire a fast projectile through a thin wall on discrete | Reproduce the tunnelling from §3, this time in the engine |
| 3.7 | Collision layers/channels | Matrix configured; only intended pairs interact |
| 3.8 | Triggers/overlaps versus blocking hits | Pickups fire once, not once per frame |

### 3C. Animation, audio, UI

| # | Milestone | Check |
|---|---|---|
| 3.9 | State machine: idle/run/jump/fall | Transitions match the mover's grounded flag |
| 3.10 | Root motion versus in-place — pick one, deliberately | Character does not slide |
| 3.11 | Audio with mixer groups and ducking | SFX audible under music |
| 3.12 | UI: UI Toolkit / UMG, bound to core state | HUD reads the core, no gameplay logic in widgets |

### 3D. Shipping

| # | Milestone | Check |
|---|---|---|
| 3.13 | Command-line build for one platform | Reproducible artifact from a clean checkout |
| 3.14 | CI running core tests on every commit | Green badge without an engine install (core tests need none) |
| 3.15 | Profile a real frame | Unity Profiler / Unreal Insights; find and fix one real cost |
| 3.16 | Ship something small and finished | A person who is not you plays it start to end |

---

## Stage 4 — The things that separate a demo from a game

| # | Milestone | Why |
|---|---|---|
| 4.1 | Save/load, including RNG state | The `Snapshot`/`Restore` on the RNG exists for this |
| 4.2 | A replay recorded as an input log | The strongest possible proof the fixed timestep is correct |
| 4.3 | Frame-perfect game feel: coyote time, jump buffering, variable jump height | Was in progress in the Python engine when the pivot happened; belongs in the core, engine-agnostic |
| 4.4 | Networking, if relevant | Unreal replication is far ahead of Unity's here |

Milestone 4.2 is the real test of everything in stage 0: if a recorded input log
does not reproduce the identical run, the determinism claim was false.

---

## Choosing between the two engines

Not a tie, and the answer depends on what is being built.

**Unity** — better for 2D, faster iteration, C# is far quicker to write and
compile, much larger asset ecosystem, better mobile story. Weaker at
photorealistic rendering, and its networking has been through several
abandoned solutions.

**Unreal** — better rendering by a wide margin (Lumen, Nanite), better
out-of-the-box character movement and networking, Blueprints genuinely useful
for designers. Costs: enormous engine, slow C++ iteration, steeper learning
curve, and 2D support (Paper2D) is a neglected corner.

For the platformer work that was underway when this pivoted: **Unity**, without
much hesitation. For a 3D action game with networking: **Unreal**.

---

## What this repository does not prove

Stated plainly so the shim builds are not mistaken for more than they are.

- No editor has been run. The adapters compile against hand-written shims; that
  verifies types and syntax, not engine behaviour.
- Nothing behind UnrealHeaderTool is verified — reflection, Blueprint exposure,
  replication, property serialisation.
- Unity lifecycle order, serialisation of `[SerializeField]`, and prefab
  behaviour are unverified.
- No rendering, audio, asset loading or build pipeline work has been done at
  all. That is stage 3, and it is the majority of what remains.
