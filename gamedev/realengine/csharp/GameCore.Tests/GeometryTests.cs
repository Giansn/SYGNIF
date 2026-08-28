using System;
using System.Collections.Generic;
using System.Numerics;
using GameCore.Geometry;
using GameCore.Random;
using Xunit;

namespace GameCore.Tests
{
    public class AabbTests
    {
        [Fact]
        public void EdgesAndCentre()
        {
            var box = Aabb.FromSize(new Vector2(10, 20), new Vector2(30, 40));
            Assert.Equal(10, box.Left);
            Assert.Equal(40, box.Right);
            Assert.Equal(20, box.Bottom);
            Assert.Equal(60, box.Top);
            Assert.Equal(new Vector2(25, 40), box.Center);
            Assert.Equal(new Vector2(15, 20), box.Extents);
        }

        [Fact]
        public void FromCenterRoundTrips()
        {
            var box = Aabb.FromCenter(new Vector2(100, 50), new Vector2(20, 10));
            Assert.Equal(new Vector2(100, 50), box.Center);
            Assert.Equal(new Vector2(90, 45), box.Min);
        }

        [Fact]
        public void RejectsInvertedCorners()
        {
            Assert.Throws<ArgumentException>(() => new Aabb(new Vector2(10, 0), new Vector2(0, 10)));
        }

        [Fact]
        public void OverlapAndSeparation()
        {
            var a = Aabb.FromSize(Vector2.Zero, new Vector2(10, 10));
            Assert.True(a.Overlaps(Aabb.FromSize(new Vector2(5, 5), new Vector2(10, 10))));
            Assert.False(a.Overlaps(Aabb.FromSize(new Vector2(20, 0), new Vector2(10, 10))));
            // Separation on a single axis is enough to miss.
            Assert.False(a.Overlaps(Aabb.FromSize(new Vector2(5, 100), new Vector2(10, 10))));
        }

        [Fact]
        public void FlushEdgesDoNotCollideByDefault()
        {
            // An entity standing exactly on a floor touches it every frame. If that
            // counted as a collision the resolver would fight it forever.
            var floor = Aabb.FromSize(new Vector2(0, 0), new Vector2(100, 10));
            var standing = Aabb.FromSize(new Vector2(0, 10), new Vector2(10, 10));
            Assert.False(standing.Overlaps(floor));
            Assert.True(standing.Overlaps(floor, touchingCounts: true));
        }

        [Fact]
        public void MinimumTranslationEjectsAlongTheShallowAxis()
        {
            // Deep in x, shallow in y: must be pushed vertically.
            var mover = Aabb.FromSize(new Vector2(0, 8), new Vector2(20, 10));
            var wall = Aabb.FromSize(new Vector2(0, 16), new Vector2(20, 10));
            var push = mover.MinimumTranslation(wall);

            Assert.NotNull(push);
            Assert.Equal(0f, push!.Value.X);
            Assert.Equal(-2f, push.Value.Y, 4);
        }

        [Fact]
        public void MinimumTranslationIsNullWhenApart()
        {
            var a = Aabb.FromSize(Vector2.Zero, new Vector2(10, 10));
            Assert.Null(a.MinimumTranslation(Aabb.FromSize(new Vector2(50, 50), new Vector2(10, 10))));
        }

        [Fact]
        public void ApplyingTheTranslationSeparatesTheBoxes()
        {
            var rng = new Pcg32(7);
            var separated = 0;

            for (var i = 0; i < 500; i++)
            {
                var a = Aabb.FromSize(
                    new Vector2(rng.Range(-20f, 20f), rng.Range(-20f, 20f)),
                    new Vector2(rng.Range(1f, 15f), rng.Range(1f, 15f)));
                var b = Aabb.FromSize(
                    new Vector2(rng.Range(-20f, 20f), rng.Range(-20f, 20f)),
                    new Vector2(rng.Range(1f, 15f), rng.Range(1f, 15f)));

                var push = a.MinimumTranslation(b);
                if (push == null)
                {
                    continue;
                }

                separated++;
                Assert.False(a.Translated(push.Value).Overlaps(b), $"{a} vs {b} push {push}");
            }

            Assert.True(separated > 20, $"only {separated} overlapping pairs; test data is not exercising this");
        }

        [Fact]
        public void CircleAgainstBoxHandlesTheCornerCaseTheShortcutGetsWrong()
        {
            // Expanding the box by the radius is the tempting shortcut and it is
            // wrong outside corners: it makes a square where the reachable region
            // is rounded.
            var box = Aabb.FromSize(Vector2.Zero, new Vector2(10, 10));
            var center = new Vector2(-3, -3);
            const float radius = 4f;

            // Nearest point is the corner, at distance sqrt(18) = 4.24 > 4.
            Assert.Equal(Math.Sqrt(18), Vector2.Distance(center, Vector2.Zero), 4);
            Assert.False(box.IntersectsCircle(center, radius));

            // The naive expanded-box test would have said yes.
            Assert.True(box.Expanded(radius).Contains(center));
        }

        [Fact]
        public void SweptBoundsCoverTheWholePath()
        {
            var bounds = Aabb.FromSize(Vector2.Zero, new Vector2(4, 4)).SweptBounds(new Vector2(100, -20));
            Assert.Equal(new Vector2(0, -20), bounds.Min);
            Assert.Equal(new Vector2(104, 4), bounds.Max);
        }
    }

    public class SweepTests
    {
        private static readonly Aabb Target = Aabb.FromSize(new Vector2(100, 100), new Vector2(50, 50));

        [Fact]
        public void DiscreteCollisionMissesAFastMover()
        {
            // The bug that motivates continuous detection. Before the step the
            // bullet is short of the wall; after it, well past. It is never inside
            // on any sampled instant, so an overlap test reports nothing and the
            // bullet passes through a solid wall.
            var wall = Aabb.FromSize(new Vector2(500, 0), new Vector2(8, 200));
            var bullet = Aabb.FromSize(new Vector2(100, 100), new Vector2(4, 4));
            var velocity = new Vector2(800, 0);

            Assert.False(bullet.Overlaps(wall));
            Assert.False(bullet.Translated(velocity).Overlaps(wall));
            Assert.True(bullet.Right < wall.Left);
            Assert.True(bullet.Translated(velocity).Left > wall.Right);
        }

        [Fact]
        public void SweptCollisionCatchesIt()
        {
            var wall = Aabb.FromSize(new Vector2(500, 0), new Vector2(8, 200));
            var bullet = Aabb.FromSize(new Vector2(100, 100), new Vector2(4, 4));
            var hit = Sweep.Against(bullet, new Vector2(800, 0), wall);

            Assert.NotNull(hit);
            Assert.Equal(396f / 800f, hit!.Value.Time, 5);
            Assert.Equal(new Vector2(-1, 0), hit.Value.Normal);
        }

        [Fact]
        public void ReportsTheCorrectNormalForEachFace()
        {
            var cases = new (Aabb Mover, Vector2 Velocity, Vector2 Expected)[]
            {
                (Aabb.FromSize(new Vector2(0, 110), new Vector2(10, 10)), new Vector2(200, 0), new Vector2(-1, 0)),
                (Aabb.FromSize(new Vector2(200, 110), new Vector2(10, 10)), new Vector2(-200, 0), new Vector2(1, 0)),
                (Aabb.FromSize(new Vector2(110, 0), new Vector2(10, 10)), new Vector2(0, 200), new Vector2(0, -1)),
                (Aabb.FromSize(new Vector2(110, 200), new Vector2(10, 10)), new Vector2(0, -200), new Vector2(0, 1)),
            };

            foreach (var (mover, velocity, expected) in cases)
            {
                var hit = Sweep.Against(mover, velocity, Target);
                Assert.True(hit.HasValue, $"no hit for velocity {velocity}");
                Assert.Equal(expected, hit!.Value.Normal);
                Assert.InRange(hit.Value.Time, 0f, 1f);
            }
        }

        [Fact]
        public void NoHitWhenMotionFallsShortOrMissesOrRetreats()
        {
            Assert.Null(Sweep.Against(Aabb.FromSize(new Vector2(0, 110), new Vector2(10, 10)), new Vector2(80, 0), Target));
            Assert.Null(Sweep.Against(Aabb.FromSize(new Vector2(0, 300), new Vector2(10, 10)), new Vector2(400, 0), Target));
            Assert.Null(Sweep.Against(Aabb.FromSize(new Vector2(0, 110), new Vector2(10, 10)), new Vector2(-200, 0), Target));
        }

        [Fact]
        public void ZeroVelocityNeverCollidesAndNeverProducesNaN()
        {
            Assert.Null(Sweep.Against(Aabb.FromSize(Vector2.Zero, new Vector2(10, 10)), Vector2.Zero, Target));

            var hit = Sweep.Against(Aabb.FromSize(new Vector2(110, 0), new Vector2(10, 10)), new Vector2(0, 500), Target);
            Assert.NotNull(hit);
            Assert.False(float.IsNaN(hit!.Value.Time));
        }

        [Fact]
        public void AlreadyOverlappingReportsImmediateContact()
        {
            var hit = Sweep.Against(Aabb.FromSize(new Vector2(110, 110), new Vector2(10, 10)), new Vector2(10, 0), Target);
            Assert.NotNull(hit);
            Assert.Equal(0f, hit!.Value.Time);
        }

        [Fact]
        public void MatchesBruteForceSampling()
        {
            // Analytic geometry is easy to get subtly wrong and hard to eyeball, so
            // check it against a stupid method that is obviously right.
            var rng = new Pcg32(1234);
            const int substeps = 4_000;
            var agreed = 0;

            for (var trial = 0; trial < 300; trial++)
            {
                var mover = Aabb.FromSize(
                    new Vector2(rng.Range(-100f, 100f), rng.Range(-100f, 100f)),
                    new Vector2(rng.Range(2f, 20f), rng.Range(2f, 20f)));
                var target = Aabb.FromSize(
                    new Vector2(rng.Range(-100f, 100f), rng.Range(-100f, 100f)),
                    new Vector2(rng.Range(2f, 40f), rng.Range(2f, 40f)));
                var velocity = new Vector2(rng.Range(-150f, 150f), rng.Range(-150f, 150f));

                if (mover.Overlaps(target))
                {
                    continue;
                }

                float? bruteTime = null;
                for (var step = 0; step <= substeps; step++)
                {
                    var t = step / (float)substeps;
                    if (mover.Translated(velocity * t).Overlaps(target))
                    {
                        bruteTime = t;
                        break;
                    }
                }

                var hit = Sweep.Against(mover, velocity, target);
                if (bruteTime == null)
                {
                    continue;
                }

                Assert.True(hit.HasValue, $"sampling hit at {bruteTime} but the sweep found nothing");
                agreed++;

                // The analytic answer is exact; sampling can only overshoot, and by
                // at most one substep.
                Assert.True(hit!.Value.Time <= bruteTime.Value + 1e-4f, $"sweep {hit.Value.Time} later than sampled {bruteTime}");
                Assert.True(hit.Value.Time > bruteTime.Value - (1f / substeps) - 1e-4f, $"sweep {hit.Value.Time} too early vs {bruteTime}");
            }

            Assert.True(agreed > 20, $"only {agreed} collisions; test data is not exercising this");
        }

        [Fact]
        public void AgainstAllReturnsTheEarliestHit()
        {
            var near = Aabb.FromSize(new Vector2(200, 0), new Vector2(10, 100));
            var far = Aabb.FromSize(new Vector2(400, 0), new Vector2(10, 100));
            var hit = Sweep.AgainstAll(
                Aabb.FromSize(new Vector2(0, 40), new Vector2(10, 10)),
                new Vector2(600, 0),
                new List<Aabb> { far, near });

            Assert.NotNull(hit);
            Assert.Equal(1, hit!.Value.Index); // index of `near`
        }
    }

    public class MoveAndSlideTests
    {
        [Fact]
        public void UnobstructedMotionCompletes()
        {
            var moved = Sweep.MoveAndSlide(
                Aabb.FromSize(Vector2.Zero, new Vector2(10, 10)),
                new Vector2(50, 30),
                Array.Empty<Aabb>());

            Assert.Equal(50f, moved.X, 3);
            Assert.Equal(30f, moved.Y, 3);
        }

        [Fact]
        public void SlidesAlongAWallInsteadOfStopping()
        {
            // Running diagonally into a vertical wall: horizontal motion is
            // cancelled, vertical motion continues. Stopping dead here is what makes
            // a game feel like it is made of glue.
            var wall = Aabb.FromSize(new Vector2(100, -100), new Vector2(20, 400));
            var hits = new List<SweepHit>();
            var moved = Sweep.MoveAndSlide(
                Aabb.FromSize(new Vector2(50, 0), new Vector2(10, 10)),
                new Vector2(80, 60),
                new[] { wall },
                hits);

            Assert.NotEmpty(hits);
            Assert.Equal(40f, moved.X, 3);
            Assert.Equal(60f, moved.Y, 3);
        }

        [Fact]
        public void DoesNotEnterTheObstacle()
        {
            var wall = Aabb.FromSize(new Vector2(100, -100), new Vector2(20, 400));
            var box = Aabb.FromSize(new Vector2(50, 0), new Vector2(10, 10));
            var moved = Sweep.MoveAndSlide(box, new Vector2(500, 0), new[] { wall });

            Assert.False(box.Translated(moved).Overlaps(wall));
        }

        [Fact]
        public void FastMoverDoesNotTunnel()
        {
            var wall = Aabb.FromSize(new Vector2(500, 0), new Vector2(8, 200));
            var bullet = Aabb.FromSize(new Vector2(100, 100), new Vector2(4, 4));
            var hits = new List<SweepHit>();
            var moved = Sweep.MoveAndSlide(bullet, new Vector2(5000, 0), new[] { wall }, hits);

            Assert.NotEmpty(hits);
            Assert.True(bullet.Translated(moved).Right < wall.Left + 1e-2f);
        }

        [Fact]
        public void InsideCornerNeedsTwoResolutions()
        {
            // Sliding off the wall pushes into the floor, so one pass is not enough.
            var wall = Aabb.FromSize(new Vector2(100, 0), new Vector2(20, 200));
            var floor = Aabb.FromSize(new Vector2(0, 100), new Vector2(200, 20));
            var box = Aabb.FromSize(new Vector2(50, 50), new Vector2(10, 10));
            var hits = new List<SweepHit>();
            var moved = Sweep.MoveAndSlide(box, new Vector2(200, 200), new[] { wall, floor }, hits);

            Assert.True(hits.Count >= 2, $"expected at least two contacts, got {hits.Count}");
            Assert.False(box.Translated(moved).Overlaps(wall));
            Assert.False(box.Translated(moved).Overlaps(floor));
        }

        [Fact]
        public void SkinIsADistanceNotAFractionOfTheStep()
        {
            // The standoff gap must not scale with speed. A slow mover and a fast
            // mover hitting the same wall must stop at the same place.
            var wall = Aabb.FromSize(new Vector2(100, -100), new Vector2(20, 400));
            var box = Aabb.FromSize(new Vector2(50, 0), new Vector2(10, 10));

            var slow = Sweep.MoveAndSlide(box, new Vector2(60, 0), new[] { wall }).X;
            var fast = Sweep.MoveAndSlide(box, new Vector2(6000, 0), new[] { wall }).X;

            Assert.Equal(slow, fast, 3);
            Assert.Equal(40f, slow, 3);
        }

        [Fact]
        public void DeadEndTerminates()
        {
            var walls = new[]
            {
                Aabb.FromSize(new Vector2(100, 0), new Vector2(20, 100)),
                Aabb.FromSize(new Vector2(0, 100), new Vector2(200, 20)),
                Aabb.FromSize(new Vector2(-20, 0), new Vector2(20, 100)),
            };

            var moved = Sweep.MoveAndSlide(Aabb.FromSize(new Vector2(50, 50), new Vector2(10, 10)), new Vector2(300, 300), walls);
            Assert.False(float.IsNaN(moved.X) || float.IsNaN(moved.Y));
            Assert.False(float.IsInfinity(moved.X) || float.IsInfinity(moved.Y));
        }
    }
}
