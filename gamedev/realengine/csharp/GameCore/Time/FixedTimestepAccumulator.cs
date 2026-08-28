using System;

namespace GameCore.Time
{
    /// <summary>
    /// Converts elapsed wall-clock time into whole fixed-size simulation ticks.
    /// </summary>
    /// <remarks>
    /// <para>
    /// Unity already does this for you and Unreal does not, which is the single
    /// most important scheduling difference between the two engines.
    /// </para>
    /// <para>
    /// In Unity, <c>FixedUpdate</c> is driven by exactly this algorithm:
    /// real time accumulates, whole <c>Time.fixedDeltaTime</c> steps are drained
    /// out, and <c>Time.maximumDeltaTime</c> (0.333 s by default) is the clamp
    /// that stops the spiral of death. So in Unity you should put deterministic
    /// gameplay in <c>FixedUpdate</c> and never re-implement this — but you do
    /// need to know it exists, because it explains why input read in
    /// <c>FixedUpdate</c> misses button presses (a tick can run zero or twice in
    /// a frame) and why <c>Update</c> is the correct place to sample input.
    /// </para>
    /// <para>
    /// In Unreal there is no gameplay equivalent. <c>AActor::Tick</c> receives a
    /// variable <c>DeltaSeconds</c>, and the fixed-step machinery
    /// (<c>bSubstepping</c>) applies to the physics solver only, not to your
    /// gameplay code. If you want reproducible gameplay in Unreal — for replays,
    /// deterministic tests, or lockstep networking — you accumulate
    /// <c>DeltaSeconds</c> yourself and step your simulation, which is precisely
    /// what this class is for.
    /// </para>
    /// <para>
    /// This is a direct port of the Python implementation in
    /// <c>gamedev/engine/loop.py</c>, including the snap tolerance, which was
    /// found by a failing test rather than by reading about it.
    /// </para>
    /// </remarks>
    public sealed class FixedTimestepAccumulator
    {
        /// <summary>
        /// How close to a whole tick the accumulator must be before it counts as whole.
        /// </summary>
        /// <remarks>
        /// Frame times are floats and essentially never land exactly on tick
        /// boundaries. A 30 Hz display against a 60 Hz tick should yield exactly
        /// two ticks per frame, but thirty frames of 1/30 s sum to
        /// 0.9999999999999999 s — 59.99999 ticks, which floors to 59. The missing
        /// tick surfaces as a one-frame hitch every few seconds: the class of bug
        /// reported as "it feels bad" and very hard to find. Snapping within a
        /// microsecond lets a tick run at most 1 us early, which is 0.006% of a
        /// 60 Hz tick, and makes integer-ratio refresh rates exact.
        /// </remarks>
        public const double SnapTolerance = 1e-6;

        private double _accumulated;

        /// <summary>Creates an accumulator.</summary>
        /// <param name="deltaTime">Fixed simulation step, in seconds.</param>
        /// <param name="maxFrameTime">
        /// Largest frame time that will be honoured. Anything longer is clamped and
        /// the excess discarded, trading a temporarily slow-motion simulation for
        /// staying responsive. Without it, a frame that takes longer to simulate
        /// than it represents queues ever more work and the game locks up.
        /// </param>
        public FixedTimestepAccumulator(double deltaTime = 1.0 / 60.0, double maxFrameTime = 0.25)
        {
            if (deltaTime <= 0.0)
            {
                throw new ArgumentOutOfRangeException(nameof(deltaTime), "must be positive");
            }

            if (maxFrameTime < deltaTime)
            {
                throw new ArgumentOutOfRangeException(nameof(maxFrameTime), "must be at least one tick");
            }

            DeltaTime = deltaTime;
            MaxFrameTime = maxFrameTime;
        }

        /// <summary>The fixed simulation step, in seconds.</summary>
        public double DeltaTime { get; }

        /// <summary>The clamp applied to incoming frame times.</summary>
        public double MaxFrameTime { get; }

        /// <summary>
        /// Total real time discarded by the clamp. Non-zero means the simulation
        /// could not keep up; it is a performance bug, not a cosmetic one.
        /// </summary>
        public double DroppedTime { get; private set; }

        /// <summary>
        /// Leftover fraction of a tick, in <c>[0, 1)</c>. Rendering interpolates
        /// between the previous and current simulation states by this amount, which
        /// is what keeps motion smooth on a display whose refresh rate has nothing
        /// to do with the tick rate.
        /// </summary>
        public double Alpha => _accumulated / DeltaTime;

        /// <summary>Adds elapsed real time and returns how many ticks should now run.</summary>
        public int Feed(double frameTime)
        {
            if (frameTime < 0.0)
            {
                throw new ArgumentOutOfRangeException(nameof(frameTime), "must not be negative");
            }

            if (frameTime > MaxFrameTime)
            {
                DroppedTime += frameTime - MaxFrameTime;
                frameTime = MaxFrameTime;
            }

            _accumulated += frameTime;
            var ticks = (int)((_accumulated + SnapTolerance) / DeltaTime);
            _accumulated -= ticks * DeltaTime;

            // Snapping can leave the remainder a few ulps below zero. A negative
            // accumulator makes Alpha negative and extrapolates rendering
            // backwards, so floor it.
            if (_accumulated < 0.0)
            {
                _accumulated = 0.0;
            }

            return ticks;
        }

        /// <summary>Discards banked time, for use after a load screen or a pause.</summary>
        public void Reset() => _accumulated = 0.0;
    }
}
