#pragma once

// FixedTimestepAccumulator — converts elapsed wall-clock time into whole
// fixed-size simulation ticks.
//
// This class matters far more in Unreal than in Unity, and the difference is
// worth being precise about.
//
// Unity implements exactly this algorithm for you: FixedUpdate is driven by an
// accumulator over Time.fixedDeltaTime, clamped by Time.maximumDeltaTime. You
// should not re-implement it there; you only need to understand it.
//
// Unreal has no gameplay equivalent. AActor::Tick receives a variable
// DeltaSeconds straight from the frame time, and the fixed-step machinery that
// does exist — substepping — belongs to the physics solver
// (PhysicsSettings: bSubstepping, MaxSubstepDeltaTime) and does not step your
// gameplay code. UEngine::bUseFixedFrameRate exists but pins the entire engine
// to a fixed rate, which is a shipping-a-cinematic tool, not a simulation tool.
//
// So in Unreal, anything that must be reproducible — replays, deterministic
// tests, lockstep multiplayer, or simply a jump that reaches the same height on
// every machine — needs code like this: accumulate DeltaSeconds in Tick, and
// step the simulation in fixed slices.
//
// Why it matters at all: with a variable timestep, Euler integration of gravity
// accumulates differently over one 32 ms step than over two 16 ms steps, so jump
// height changes with frame rate and a player on a slow machine literally cannot
// make a jump that a fast machine clears.
//
// No exceptions (Unreal game modules build with bEnableExceptions = false):
// invalid configuration asserts in debug and is clamped to something sane in
// shipping, rather than throwing.

#include <cassert>

namespace gamecore
{

class FixedTimestepAccumulator
{
public:
    // How close to a whole tick the accumulator must be before it counts as
    // whole.
    //
    // Frame times are floats and essentially never land exactly on tick
    // boundaries. A 30 Hz display against a 60 Hz tick should give exactly two
    // ticks per frame, but thirty frames of 1/30 s sum to 0.9999999999999999 s
    // — 59.99999 ticks, which floors to 59. The missing tick surfaces as a
    // one-frame hitch every few seconds: reported as "it feels bad", and
    // miserable to track down. Snapping within a microsecond lets a tick run at
    // most 1 us early, which is 0.006% of a 60 Hz tick.
    static constexpr double SnapTolerance = 1e-6;

    explicit FixedTimestepAccumulator(double InDeltaTime = 1.0 / 60.0, double InMaxFrameTime = 0.25)
    {
        assert(InDeltaTime > 0.0 && "delta time must be positive");
        assert(InMaxFrameTime >= InDeltaTime && "max frame time must be at least one tick");

        DeltaTime = InDeltaTime > 0.0 ? InDeltaTime : 1.0 / 60.0;
        MaxFrameTime = InMaxFrameTime >= DeltaTime ? InMaxFrameTime : DeltaTime;
    }

    // Adds elapsed real time; returns how many ticks should now run.
    int Feed(double FrameTime)
    {
        assert(FrameTime >= 0.0 && "frame time must not be negative");
        if (FrameTime < 0.0)
        {
            return 0;
        }

        // The clamp is the guard against the spiral of death: if a tick takes
        // longer to compute than it represents, each frame requests more ticks
        // than the last and the game locks up. Discarding the excess trades a
        // temporarily slow-motion simulation for staying responsive, which is
        // the right trade — a game at half speed is playable, a frozen one is
        // not.
        if (FrameTime > MaxFrameTime)
        {
            DroppedTime += FrameTime - MaxFrameTime;
            FrameTime = MaxFrameTime;
        }

        Accumulated += FrameTime;
        const int Ticks = static_cast<int>((Accumulated + SnapTolerance) / DeltaTime);
        Accumulated -= Ticks * DeltaTime;

        // Snapping can leave the remainder a few ulps below zero. A negative
        // accumulator makes Alpha negative and extrapolates rendering backwards.
        if (Accumulated < 0.0)
        {
            Accumulated = 0.0;
        }

        return Ticks;
    }

    // Leftover fraction of a tick, in [0, 1). Rendering interpolates between the
    // previous and current simulation states by this amount, which keeps motion
    // smooth on a display whose refresh rate has nothing to do with the tick
    // rate.
    double Alpha() const { return Accumulated / DeltaTime; }

    double GetDeltaTime() const { return DeltaTime; }
    double GetMaxFrameTime() const { return MaxFrameTime; }

    // Non-zero means the simulation could not keep up. It is a performance bug,
    // not a cosmetic one, and worth surfacing rather than hiding.
    double GetDroppedTime() const { return DroppedTime; }

    // Discards banked time — after a load screen or a pause, where the elapsed
    // wall time is real but meaningless.
    void Reset() { Accumulated = 0.0; }

private:
    double DeltaTime = 1.0 / 60.0;
    double MaxFrameTime = 0.25;
    double Accumulated = 0.0;
    double DroppedTime = 0.0;
};

} // namespace gamecore
