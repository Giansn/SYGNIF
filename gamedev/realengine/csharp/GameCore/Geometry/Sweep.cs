using System;
using System.Collections.Generic;
using System.Numerics;

namespace GameCore.Geometry
{
    /// <summary>The outcome of a continuous collision query.</summary>
    public readonly struct SweepHit
    {
        /// <summary>
        /// Fraction of the attempted motion completed before contact, in <c>[0, 1]</c>.
        /// So <c>start + velocity * Time</c> is where the mover should stop.
        /// </summary>
        public readonly float Time;

        /// <summary>Unit vector pointing out of the surface that was struck.</summary>
        public readonly Vector2 Normal;

        /// <summary>Index of the obstacle hit, when the query was against a collection; otherwise -1.</summary>
        public readonly int Index;

        /// <summary>Creates a hit record.</summary>
        public SweepHit(float time, Vector2 normal, int index = -1)
        {
            Time = time;
            Normal = normal;
            Index = index;
        }

        /// <inheritdoc/>
        public override string ToString() => $"SweepHit(t={Time:0.####}, n={Normal}, i={Index})";
    }

    /// <summary>Continuous collision detection and response for axis-aligned boxes.</summary>
    /// <remarks>
    /// <para>
    /// Both Unity and Unreal ship this, and in production you should normally use
    /// theirs — <c>Physics2D.BoxCast</c> / <c>Rigidbody2D.Slide</c> in Unity,
    /// <c>UWorld::SweepSingleByChannel</c> and <c>CharacterMovementComponent</c> in
    /// Unreal. It is implemented here for two reasons that survive that fact.
    /// </para>
    /// <para>
    /// First, understanding it is what lets you configure theirs correctly. Unity's
    /// <c>Rigidbody</c> ships with <c>Collision Detection</c> set to
    /// <c>Discrete</c>, and every project eventually files the same bug: a fast
    /// projectile passes through a thin wall. Knowing that discrete detection asks
    /// "do these overlap right now" — a question a bullet that was in front on one
    /// tick and behind on the next never answers yes to — is what turns that from
    /// a mystery into a one-setting fix.
    /// </para>
    /// <para>
    /// Second, gameplay code that must be deterministic and unit-testable cannot
    /// call into either physics engine, because both are stateful, both need a
    /// live scene, and neither is bit-reproducible across platforms. Anything a
    /// replay or a test depends on has to live in code like this.
    /// </para>
    /// </remarks>
    public static class Sweep
    {
        /// <summary>
        /// Finds when, during a motion, <paramref name="mover"/> first touches <paramref name="target"/>.
        /// </summary>
        /// <remarks>
        /// The slab method. Treat the target as two slabs, one per axis; for each,
        /// compute when the mover enters and leaves it. The boxes overlap during
        /// the window where both slabs are entered, so contact begins at
        /// <c>max(entryX, entryY)</c> and ends at <c>min(exitX, exitY)</c>. If the
        /// window starts after it ends, the mover passes by without touching.
        /// </remarks>
        /// <returns>The hit, or <c>null</c> if there is no contact within this step.</returns>
        public static SweepHit? Against(Aabb mover, Vector2 velocity, Aabb target)
        {
            float xEntryDistance, xExitDistance, yEntryDistance, yExitDistance;

            if (velocity.X > 0f)
            {
                xEntryDistance = target.Min.X - mover.Max.X;
                xExitDistance = target.Max.X - mover.Min.X;
            }
            else
            {
                xEntryDistance = target.Max.X - mover.Min.X;
                xExitDistance = target.Min.X - mover.Max.X;
            }

            if (velocity.Y > 0f)
            {
                yEntryDistance = target.Min.Y - mover.Max.Y;
                yExitDistance = target.Max.Y - mover.Min.Y;
            }
            else
            {
                yEntryDistance = target.Max.Y - mover.Min.Y;
                yExitDistance = target.Min.Y - mover.Max.Y;
            }

            float xEntry, xExit, yEntry, yExit;

            // A zero-velocity axis never enters or leaves its slab. If the boxes
            // already overlap on that axis the constraint holds for all time; if
            // not, contact is impossible. Dividing by zero here instead produces a
            // NaN that silently poisons the entire physics step — and NaN compares
            // false against everything, so the collision simply stops happening
            // with no error anywhere.
            if (velocity.X == 0f)
            {
                if (mover.Max.X <= target.Min.X || mover.Min.X >= target.Max.X)
                {
                    return null;
                }

                xEntry = float.NegativeInfinity;
                xExit = float.PositiveInfinity;
            }
            else
            {
                xEntry = xEntryDistance / velocity.X;
                xExit = xExitDistance / velocity.X;
            }

            if (velocity.Y == 0f)
            {
                if (mover.Max.Y <= target.Min.Y || mover.Min.Y >= target.Max.Y)
                {
                    return null;
                }

                yEntry = float.NegativeInfinity;
                yExit = float.PositiveInfinity;
            }
            else
            {
                yEntry = yEntryDistance / velocity.Y;
                yExit = yExitDistance / velocity.Y;
            }

            var entryTime = Math.Max(xEntry, yEntry);
            var exitTime = Math.Min(xExit, yExit);

            if (entryTime > exitTime || entryTime > 1f || exitTime <= 0f)
            {
                return null;
            }

            if (xEntry > 1f || yEntry > 1f)
            {
                return null;
            }

            // Starting already overlapped reports immediate contact rather than
            // pretending nothing is wrong, so the caller can push out.
            entryTime = Math.Max(entryTime, 0f);

            // Whichever axis was entered last is the face that was struck.
            Vector2 normal = xEntry > yEntry
                ? new Vector2(velocity.X > 0f ? -1f : 1f, 0f)
                : new Vector2(0f, velocity.Y > 0f ? -1f : 1f);

            return new SweepHit(entryTime, normal);
        }

        /// <summary>Finds the earliest contact against any of <paramref name="targets"/>.</summary>
        /// <remarks>
        /// Only the first hit matters: everything after it lies on a trajectory the
        /// mover never actually takes.
        /// </remarks>
        public static SweepHit? AgainstAll(Aabb mover, Vector2 velocity, IReadOnlyList<Aabb> targets)
        {
            if (targets == null)
            {
                throw new ArgumentNullException(nameof(targets));
            }

            SweepHit? earliest = null;
            for (var i = 0; i < targets.Count; i++)
            {
                var hit = Against(mover, velocity, targets[i]);
                if (hit.HasValue && (!earliest.HasValue || hit.Value.Time < earliest.Value.Time))
                {
                    earliest = new SweepHit(hit.Value.Time, hit.Value.Normal, i);
                }
            }

            return earliest;
        }

        /// <summary>
        /// Moves as far as possible along <paramref name="velocity"/>, sliding along
        /// whatever is struck, and returns the motion actually performed.
        /// </summary>
        /// <param name="box">Starting bounds of the mover.</param>
        /// <param name="velocity">Attempted motion for this step, in world units.</param>
        /// <param name="obstacles">Solids to collide against.</param>
        /// <param name="hits">Receives the contacts encountered, in order.</param>
        /// <param name="maxIterations">
        /// Cap on resolution passes. Sliding off one surface can push into another —
        /// an inside corner needs two in a single step — and the cap stops a
        /// wedge-shaped dead end from looping forever.
        /// </param>
        /// <param name="skin">
        /// Gap left at the contact point, <b>in world units, not as a fraction of the
        /// step</b>. Landing exactly on a surface means the next step starts flush
        /// against it, and floating point then decides at random whether that counts
        /// as touching, which shows up as an entity flickering between grounded and
        /// airborne. Expressing it as a fraction is a trap: the standoff would scale
        /// with speed, so a bullet stops visibly short of the wall it hit while a
        /// slow walker is left touching it.
        /// </param>
        /// <remarks>
        /// Stopping dead on contact makes a game feel like it is made of glue:
        /// brush a wall while running diagonally and you halt instead of continuing
        /// along it. Sliding keeps the component of motion parallel to the surface
        /// and discards only the component heading into it.
        /// </remarks>
        public static Vector2 MoveAndSlide(
            Aabb box,
            Vector2 velocity,
            IReadOnlyList<Aabb> obstacles,
            List<SweepHit>? hits = null,
            int maxIterations = 4,
            float skin = 1e-4f)
        {
            if (obstacles == null)
            {
                throw new ArgumentNullException(nameof(obstacles));
            }

            var remaining = velocity;
            var total = Vector2.Zero;
            var current = box;

            for (var iteration = 0; iteration < maxIterations; iteration++)
            {
                var speed = remaining.Length();
                if (speed == 0f)
                {
                    break;
                }

                var hit = AgainstAll(current, remaining, obstacles);
                if (!hit.HasValue)
                {
                    total += remaining;
                    break;
                }

                var safeTime = Math.Max(0f, hit.Value.Time - (skin / speed));
                var step = remaining * safeTime;
                total += step;
                current = current.Translated(step);
                hits?.Add(hit.Value);

                // Project the leftover motion onto the surface: drop the component
                // heading into it, keep the component running along it.
                var leftover = remaining * (1f - safeTime);
                remaining = leftover - (hit.Value.Normal * Vector2.Dot(leftover, hit.Value.Normal));
            }

            return total;
        }
    }
}
