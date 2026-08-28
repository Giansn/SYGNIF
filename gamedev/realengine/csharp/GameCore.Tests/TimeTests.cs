using System;
using System.Numerics;
using GameCore.Time;
using Xunit;

namespace GameCore.Tests
{
    public class FixedTimestepAccumulatorTests
    {
        [Fact]
        public void ExactTickCounts()
        {
            var accumulator = new FixedTimestepAccumulator(1.0 / 60.0);
            Assert.Equal(1, accumulator.Feed(1.0 / 60.0));
            Assert.Equal(4, accumulator.Feed(4.0 / 60.0));
            Assert.Equal(0, accumulator.Feed(0.0));
        }

        [Fact]
        public void SubTickFramesBankUntilTheyMakeAWholeTick()
        {
            // A 90 Hz frame is *shorter* than a 60 Hz tick, so the first one cannot
            // produce a tick at all. Three of them are exactly two ticks' worth.
            var accumulator = new FixedTimestepAccumulator(1.0 / 60.0);
            Assert.Equal(0, accumulator.Feed(1.0 / 90.0));
            Assert.Equal(1, accumulator.Feed(1.0 / 90.0));
            Assert.Equal(1, accumulator.Feed(1.0 / 90.0));
        }

        [Fact]
        public void HalfRateDisplayYieldsExactlyTwoTicksEveryFrame()
        {
            // The regression the snap tolerance exists for. Without it, thirty
            // frames of 1/30 s sum to 0.9999999999999999 s, which floors to 59
            // ticks instead of 60 and surfaces as a periodic one-frame hitch.
            var accumulator = new FixedTimestepAccumulator(1.0 / 60.0);
            for (var i = 0; i < 600; i++)
            {
                Assert.Equal(2, accumulator.Feed(1.0 / 30.0));
            }
        }

        [Fact]
        public void AlphaIsTheLeftoverFraction()
        {
            var accumulator = new FixedTimestepAccumulator(1.0 / 60.0);
            accumulator.Feed(1.5 / 60.0);
            Assert.Equal(0.5, accumulator.Alpha, 9);
            Assert.InRange(accumulator.Alpha, 0.0, 0.999999);
        }

        [Fact]
        public void NoDriftOverTenMinutes()
        {
            var accumulator = new FixedTimestepAccumulator(1.0 / 60.0);
            var total = 0;
            for (var i = 0; i < 36_000; i++)
            {
                total += accumulator.Feed(1.0 / 60.0);
            }

            Assert.Equal(36_000, total);
        }

        [Fact]
        public void ClampPreventsTheSpiralOfDeath()
        {
            // A ten-second stall must not queue 600 ticks; simulating them would
            // take longer than the stall itself and the game would never recover.
            var accumulator = new FixedTimestepAccumulator(1.0 / 60.0, maxFrameTime: 0.25);
            Assert.Equal(15, accumulator.Feed(10.0));
            Assert.Equal(9.75, accumulator.DroppedTime, 6);
        }

        [Fact]
        public void RejectsNonsenseConfiguration()
        {
            Assert.Throws<ArgumentOutOfRangeException>(() => new FixedTimestepAccumulator(0.0));
            Assert.Throws<ArgumentOutOfRangeException>(() => new FixedTimestepAccumulator(1.0 / 60.0, 0.001));
            Assert.Throws<ArgumentOutOfRangeException>(() => new FixedTimestepAccumulator().Feed(-1.0));
        }

        [Fact]
        public void VariableTimestepChangesThePhysics()
        {
            // The bug the fixed timestep exists to prevent: the same one second of
            // gravity, integrated at two frame rates, lands in two different places.
            // In a real game that is the difference between clearing a jump and not.
            var coarse = Integrate(1.0f / 15.0f, 15);
            var fine = Integrate(1.0f / 240.0f, 240);

            Assert.Equal(coarse.X, fine.X, 3); // no acceleration on x
            Assert.True(Math.Abs(coarse.Y - fine.Y) > 0.1f, $"expected divergence, got {coarse.Y} vs {fine.Y}");
        }

        [Fact]
        public void FixedTimestepIsFrameRateIndependent()
        {
            // The fix: drive the same simulation from two wildly different frame
            // rates and the tick counts, and therefore the results, are identical.
            var smooth = new FixedTimestepAccumulator(1.0 / 60.0);
            var stuttery = new FixedTimestepAccumulator(1.0 / 60.0);

            var smoothTicks = 0;
            for (var i = 0; i < 60; i++)
            {
                smoothTicks += smooth.Feed(1.0 / 60.0);
            }

            var stutteryTicks = 0;
            for (var i = 0; i < 15; i++)
            {
                stutteryTicks += stuttery.Feed(4.0 / 60.0);
            }

            Assert.Equal(60, smoothTicks);
            Assert.Equal(60, stutteryTicks);
            Assert.Equal(Integrate(1.0f / 60.0f, smoothTicks), Integrate(1.0f / 60.0f, stutteryTicks));
        }

        private static Vector2 Integrate(float dt, int steps)
        {
            var position = Vector2.Zero;
            var velocity = new Vector2(10f, 20f);
            for (var i = 0; i < steps; i++)
            {
                velocity += new Vector2(0f, -9.81f) * dt;
                position += velocity * dt;
            }

            return position;
        }
    }
}
