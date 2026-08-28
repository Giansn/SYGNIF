# Learning journal

What I actually got wrong, and what fixed it. Ordered as it happened.

Every entry here was found by a failing test or by looking at rendered output,
not by reading about it. That distinction is the point of the exercise: the
things below are exactly the things that a tutorial states in one sentence and
that you do not really believe until your own code does the wrong thing.

---

## Phase 1 — Building a 2D engine from scratch (Python, stdlib only)

### 1. The accumulator silently dropped a tick

**Symptom.** A test asserting that 30 frames at 1/30 s produce 60 ticks at a
60 Hz timestep failed with 59.

**Diagnosis.** Not a logic error. Thirty float values of `1/30` sum to
`0.9999999999999999`, which is 59.99999 ticks, which floors to 59. The
accumulator was arithmetically correct and the *input* was the problem: float
frame times essentially never land on tick boundaries.

**Why it matters.** Left alone this is not a rounding curiosity. A display
running at exactly half the tick rate — a very common case — loses a tick
periodically, which surfaces as a one-frame hitch every few seconds. That is
the class of bug reported as "the game feels bad" and it is miserable to trace.

**Fix.** A 1 µs snap tolerance when converting accumulated time to whole ticks.
A tick can now run at most 1 µs early, which is 0.006% of a 60 Hz tick, and
integer-ratio refresh rates become exact. Carried into the C# and C++ ports.

### 2. `skin` was a fraction of the step, not a distance

**Symptom.** A slide test expected a mover to stop at exactly 40 units from its
start and it stopped at 39.992.

**Diagnosis.** The standoff gap left at contact was computed as a fraction of
the attempted motion. So the gap scaled with speed: a bullet moving 5000 units
per step stopped half a unit short of the wall while a slow walker was left
essentially touching it.

**Fix.** Express the skin in world units and convert to time by dividing by
speed. Both ports have a dedicated regression test asserting that a slow mover
and a fast mover hitting the same wall stop in the same place.

### 3. Two failing tests that were the test's fault, not the code's

Worth recording because "the test is wrong" is a conclusion that has to be
earned, not assumed.

- **Bresenham "gap".** A line test reported a gap at x=14. The line ran off the
  bottom of a 24×24 surface and was correctly clipped; clipping looks exactly
  like a gap. Fixed by sizing the surface so no test line escapes — and by
  adding an assertion that the endpoints are in bounds, which then immediately
  caught a *second* bad case I had written.
- **Sub-tick frames.** I asserted that a 1/90 s frame yields one tick at 60 Hz.
  It yields zero: a 90 Hz frame is *shorter* than a 60 Hz tick.

### 4. Pong: two competent opponents made the game unfinishable

**Symptom.** A full match between two AI paddles ran for 600 simulated seconds
with no winner. The longest rally was 418 hits.

**Diagnosis.** Both paddles aim at the predicted intercept. A centred hit
returns the ball flat down the middle, which puts it straight back at the other
paddle's rest position, which returns it flat again. The ball speed was capped,
so the rally reached a stable fixed point that neither side could break.

**The general lesson.** A rally needs a guaranteed terminating condition. The
escalation has to eventually exceed what a defender can physically reach,
otherwise "both players are good" means "the game never ends".

**Fix, chosen by measurement rather than taste.** Twelve seeds per setting,
five-point matches between equally skilled AIs:

| ball speed cap | mean match | worst match | longest rally |
|---|---|---|---|
| 700 | 431 s | 599 s | 251 hits |
| 900 | 256 s | 306 s | 89 hits |
| 1100 | 212 s | 258 s | 48 hits |

900 keeps rallies dramatic while bounding a match at about five minutes.

### 5. Pong: the AI's intercept prediction was wrong by half a ball, twice

**Symptom.** The closed-form intercept predictor disagreed with simulation by
13.8 px after a couple of wall bounces.

**Diagnosis.** Two independent off-by-half-a-ball errors:

1. It folded the ball's **centre** over the full field height. The ball is a
   5 px box that bounces when its **edge** meets the wall, so the centre only
   ever travels within `[half, height − half]`. Error compounds per bounce.
2. It computed travel time to the paddle's **face**, but contact happens when
   the ball's **leading edge** arrives — half a ball-width earlier.

**Fix.** Fold over the centre's real range and aim at the contact plane.
Verified against an independent step-by-step reflection simulation across four
trajectories, agreeing to within 0.5 px.

### 6. Checking for a bias I suspected and did not have

The first five seeds all produced a win for the left paddle. Rather than assume
coincidence or assume a bug, I ran 24 seeds three ways: default (13/11), AI
seeds swapped between sides (10/14), and identical AI seeds on both sides
(12/12 exactly). No bias. Five in a row was chance at roughly 3%.

Recorded because the instinct to "fix" a non-existent asymmetry would have
introduced a real one.

---

## Phase 2 — Pivot to Unity and Unreal preparation

The from-scratch engine reached four complete modules (loop, renderer,
collision, Pong) plus a tilemap, when the direction changed to preparing for a
real engine. See `realengine/` for that work; the notes below are what the
transition itself taught.

### 7. About two thirds of the hand-built engine is correctly thrown away

The renderer, PNG encoder, bitmap font, sprite system, and loop driver all have
better equivalents in both engines. Rebuilding any of them against a real engine
would be a mistake.

What survives is precisely the part that has to be **deterministic and
testable**: the fixed timestep, the seeded RNG, swept collision, the tile grid's
gameplay queries. That is not a coincidence. It is the part both engines either
omit or provide only in a form that needs a live scene, and therefore the part
that is hardest to get right and impossible to unit-test through the engine.

### 8. The coordinate convention does not survive a port, and fails silently

The Python engine used Y-down throughout because it drew straight into a
framebuffer. Unity 2D is Y-up; Unreal is Z-up with a side-on game on the XZ
plane. Getting this wrong is **not a compile error** — it is gravity pulling
sideways or a level loading upside down.

Handled structurally rather than by care: boxes stored corner-to-corner
(`Min`/`Max`) so they carry no convention, the ASCII-level flip done exactly
once at load, and the axis mapping confined to one file per engine.

### 9. Strict build settings found defects that compile fine otherwise

Compiling the C++ core the way Unreal compiles a game module
(`-fno-exceptions -fno-rtti`, `-Werror` with `-Wconversion`, `-Wfloat-equal`,
`-Wshadow`, `-Wold-style-cast`) surfaced:

- Implicit `int`→`float` conversions across the tile grid.
- Exact float comparison. The divide-by-zero guards genuinely want `==`, so
  they now route through one documented `IsExactlyZero` helper — the wrong fix
  would have been an epsilon, which would reject motion that is merely very slow.
- **A portability bug in my own test.** RNG draws written inline as constructor
  arguments depend on C++'s *unspecified* argument evaluation order, so the same
  test could assign the stream differently on another compiler. g++ and clang++
  now report identical check counts, which is the proof the fix worked.

### 10. `file(GLOB)` in CMake let a build pass while proving nothing

Adding a new source file did not compile it: CMake expanded the glob once, at
configure time. The build was green and the file I had just written was never
touched. `CONFIGURE_DEPENDS` fixes it.

A green build that silently skips your work is worse than a failing one.

### 11. A shim can invent problems that the real thing does not have

`-Wshadow` fired on locals named `Owner` in the Unreal module — because my
hand-written shim named the member `Owner`, where the real engine names it
`OwnerPrivate`. The warning would never occur in an actual Unreal build.

Fixed the shim, not the module code. When a stand-in disagrees with the thing it
stands in for, the stand-in is usually what is wrong, and "fixing" the real code
to satisfy it makes the code worse for no reason.

---

## Things I would tell myself at the start

1. **Write the fixed timestep first and never revisit the decision.** Nearly
   every "physics is inconsistent" symptom traces back to it.
2. **Build a way to look at your output early.** The software renderer paid for
   itself immediately — three layout bugs in the very first test card were
   obvious in a picture and would have been invisible in assertions.
3. **Cross-check analytic geometry against a brute-force version.** Swept AABB
   is short, plausible-looking, and easy to get subtly wrong. Sampling the
   motion at ten thousand points is obviously correct and makes a fine oracle.
4. **Tune by measuring.** The Pong speed cap looked like a taste question and
   was decided by a table of twelve seeds in about a minute.
5. **When a test fails, the test is a suspect too** — but make it prove its
   innocence rather than assuming it.
