# Port plan: hand-built engine → Unity and Unreal

What happens to each thing I built from scratch when a real engine is doing the
work. The useful question is not "how do I translate this code" but "does the
engine already own this, and if so, am I allowed to keep an opinion about it".

Three verdicts run through the table below:

- **Delete it.** The engine owns this and does it better. Rebuilding is how you
  end up maintaining a worse renderer instead of making a game.
- **Keep it.** The engine either does not provide it, or provides a version that
  cannot be used from testable, deterministic code.
- **Keep it, but wrap.** The logic stays engine-free; a thin adapter connects it.

---

## 1. Subsystem mapping

| Subsystem | Hand-built (Python) | Unity | Unreal | Verdict |
|---|---|---|---|---|
| Fixed timestep | `engine/loop.py` accumulator + snap tolerance | `FixedUpdate`, `Time.fixedDeltaTime`, `Time.maximumDeltaTime` | **nothing equivalent** — `Tick` gets raw frame delta; substepping drives Chaos only | Unity: delete, understand. Unreal: **keep** |
| Deterministic RNG | PCG32 | `UnityEngine.Random` is a global the engine itself draws from | `FRandomStream` is seedable but is an LCG, version-tied | **Keep** in both |
| Vector maths | `Vec2` | `Vector2`, `Mathf` | `FVector2D`, `FMath` | **Delete** — but keep a core-side type for the engine-free layer |
| AABB / swept collision | `engine/collision.py` | `Physics2D.BoxCast`, colliders | `UWorld::SweepSingleByChannel` | **Keep, but wrap** — engine version is untestable and non-deterministic |
| Move-and-slide | `move_and_slide` | `Rigidbody2D.Slide` (2022+), `CharacterController.Move` | `UCharacterMovementComponent` | **Keep, but wrap** for precise 2D; otherwise delete |
| Tile map | `engine/tilemap.py` | `Tilemap` + `TilemapCollider2D` | Paper2D tile maps | **Keep, but wrap** — engine owns rendering/colliders, core owns gameplay queries |
| Input edge detection | `engine/input.py` | `Input.GetKeyDown`, Input System package | Enhanced Input | **Keep, but wrap** — see §3 |
| Software renderer | `render/surface.py`, `render/png.py` | SpriteRenderer, URP | Paper2D, Niagara, Slate | **Delete.** Unambiguously |
| PNG encoder | `render/png.py` | built in | built in | **Delete** |
| Bitmap font | `render/font.py` | TextMeshPro | Slate / UMG | **Delete** |
| Game loop driver | `GameLoop` | engine owns the loop | engine owns the loop | **Delete** |
| Sprites / animation | `Sprite.from_ascii` | Sprite, Animator | Flipbooks, Anim Blueprints | **Delete** |
| Scene / entity model | ad hoc classes | GameObject + Component | AActor + UActorComponent | **Delete** |
| Asset loading | none (source-embedded) | AssetDatabase, Addressables | UAsset, Asset Registry | **Delete** — and learn properly |
| Audio | none | AudioSource | MetaSounds | **Delete** |
| Physics (rigid bodies, joints) | none | Box2D (2D), PhysX/Unity Physics | Chaos | **Delete** |
| Pathfinding | not reached before the pivot | NavMesh, A* packages | Navigation System, `UNavigationSystemV1` | **Delete** for navigation; keep for grid puzzles |

The honest summary: roughly two thirds of what I built by hand is thrown away
against a real engine, and that is the correct outcome. It was never going to
out-render URP. The third that survives is the third that had to be
deterministic and testable — which is exactly the part that is hardest to get
right and that the engines deliberately do not solve for you.

---

## 2. The architectural rule everything else follows

**Gameplay logic goes in a library with no engine reference. The engine layer is
adapters.**

This is not a purity argument, it is a feedback-loop argument. Concretely, in
this repository:

```
GameCore (netstandard2.1, no UnityEngine)   68 tests, 125 ms
gamecore (C++17, no Unreal headers)         38 tests, 1542 checks, ~10 ms
```

The same logic living inside a `MonoBehaviour` or an `AActor` is testable only
by entering play mode or running Automation tests through the editor — call it
30 seconds to several minutes per iteration, and impossible in a plain CI job
without an engine install and a licence. The difference between a 0.1 second
loop and a 60 second loop is the difference between writing tests and not.

Both engines provide a mechanism to *enforce* this, and both are worth turning
on immediately:

- **Unity:** an assembly definition with `"noEngineReferences": true`. Adding a
  `UnityEngine` using directive to a core file becomes a compile error.
- **Unreal:** a separate UBT module that does not list `Engine` or `CoreUObject`
  in its dependencies.

Without enforcement the boundary erodes — someone reaches for `Mathf.Clamp`,
then `Debug.Log`, then `Time.deltaTime`, and a year later the core needs a
running engine and nobody can point at when that happened.

### What must NOT go in the core

- Anything needing a frame, a scene, or an asset.
- Rendering, audio, and input *polling* (input *state* is fine).
- Anything the engine already does well — the core is not a second engine.

---

## 3. Where the two engines genuinely differ

Most differences are naming. These four are real and change design.

### 3.1 Fixed timestep — the big one

**Unity** runs `FixedUpdate` on an accumulator over `Time.fixedDeltaTime`,
clamped by `Time.maximumDeltaTime` (0.333 s). Put deterministic gameplay there.

**Unreal has no gameplay equivalent.** `AActor::Tick` receives the raw frame
delta. `bSubstepping` in Physics settings steps *Chaos*, not your code.
`UEngine::bUseFixedFrameRate` pins the whole engine to a fixed rate, which makes
the game run in slow motion whenever a frame overruns — a tool for rendering
cinematics, not for gameplay.

So: `USygnifFixedStepComponent` is mandatory scaffolding in Unreal and
redundant in Unity. Both are in this repo, and the C# accumulator is retained
for the case where gameplay needs a tick rate *different* from the physics rate.

### 3.2 Input sampling

Unity's `Input.GetKeyDown` is true for a **render frame**. `FixedUpdate` runs
zero, one, or several times per render frame. Reading edge-triggered input
inside `FixedUpdate` therefore both **drops** presses (a frame with no fixed
tick) and **repeats** them (a frame with three). Both failures are frame-rate
dependent, so they reproduce on the tester's machine and not on yours.

The fix — sample in `Update`, latch, consume in the tick — is what
`ActionState` + `UnityInputRelay` implement.

Unreal's Enhanced Input is event-driven and less prone to this, but the moment
you add a fixed-step component the same latching is required for the same
reason.

### 3.3 Coordinate conventions

| | Up axis | 2D plane | Notes |
|---|---|---|---|
| Python original | **−Y** (screen) | XY | Y grows downward, framebuffer convention |
| Unity 2D | **+Y** | XY | Z is sprite sort depth |
| Unreal | **+Z** | XZ side-on, XY top-down | left-handed, X forward |

This is the single most error-prone part of the port, because getting it wrong
is **not a compile error** — it is gravity pulling sideways, or levels loading
upside down. Handled by: storing boxes corner-to-corner (`Min`/`Max`) so they
are convention-neutral, doing the ASCII-level flip exactly once at load, and
putting the axis mapping in one file (`SygnifCoreTypes.h`).

### 3.4 Memory and allocation

**Unity (C#)** is garbage collected. Per-frame allocation is the most common
avoidable performance defect: it does not show as a steady cost but as periodic
hitching when a collection runs. Hence math types as `readonly struct`, and
reusing collection buffers across frames rather than allocating per query.

**Unreal (C++)** has no GC for plain C++ types but a real one for `UObject`s.
Rules that bite: a raw `UObject*` member not marked `UPROPERTY()` will not be
tracked and can be collected while you hold it — a dangling pointer with no
diagnostic. Engine-free core types are not `UObject`s and are exempt, which is
another quiet argument for the split.

---

## 4. What carries over unchanged

These ported line-for-line and are now verified in all three languages against
the same expected values:

- **PCG32.** Python, C# and C++ produce a byte-identical stream from the same
  seed. Tested by asserting the C# and C++ implementations against values
  generated by the Python one. This is what lets a world seed mean the same
  thing in a tool, a test and the shipped game.
- **The accumulator, including the snap tolerance.** The 1 µs snap was found by
  a failing Python test (30 frames of 1/30 s sum to 0.9999999999999999 s, which
  floors to 59 ticks instead of 60) and is present in all three.
- **Swept AABB.** Same slab method, same zero-velocity guard, same
  brute-force cross-check in each language's test suite.
- **`skin` as a distance, not a fraction.** Found by a failing Python test; the
  C# and C++ ports have a dedicated regression test asserting a slow mover and
  a fast mover stop at the same place.

---

## 5. Migration order

1. **Core library first, with tests.** Done: `csharp/GameCore`, `cpp/`.
2. **Enforce the boundary.** Done: asmdef `noEngineReferences`; UBT module split
   documented in `SygnifGame.Build.cs`.
3. **Thinnest possible adapters.** Done: input relay, kinematic mover,
   fixed-step component.
4. **Then learn the engine's own subsystems** — rendering, assets, animation,
   audio, UI, build pipeline. None of that belongs in the core, and all of it is
   still ahead. See `LEARNING_PATH.md`.

The ordering matters. Starting at step 4 is the common path and it is how you
end up with gameplay logic welded into `MonoBehaviour`s that can only be tested
by playing the game.
