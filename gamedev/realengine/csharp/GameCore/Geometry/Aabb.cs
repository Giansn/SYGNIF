using System;
using System.Numerics;

namespace GameCore.Geometry
{
    /// <summary>
    /// An axis-aligned bounding box, stored as its minimum and maximum corners.
    /// </summary>
    /// <remarks>
    /// <para>
    /// <b>Why a struct.</b> Math types in gameplay code are allocated in enormous
    /// numbers — every collision query builds several. In Unity a class here would
    /// put that traffic on the managed heap and show up as periodic GC spikes,
    /// which read to a player as stutter. A <c>readonly struct</c> lives on the
    /// stack, costs nothing to create and cannot be aliased, which also removes
    /// the bug where two entities accidentally share one box and moving either
    /// moves both.
    /// </para>
    /// <para>
    /// <b>Why min/max instead of position and size.</b> The Python original used
    /// a top-left corner plus a size because it was drawing into a framebuffer,
    /// where Y grows downward. Unity's 2D convention is Y-up and Unreal's is
    /// Z-up, so a "top-left plus size" box does not port: the meaning of every
    /// sign flips. Corner-to-corner is convention-neutral — <see cref="Min"/> is
    /// simply the smaller corner on both axes — so the same code is correct in
    /// either engine. Mixing conventions between physics and rendering is one of
    /// the great time sinks of game programming, and this sidesteps it.
    /// </para>
    /// </remarks>
    public readonly struct Aabb : IEquatable<Aabb>
    {
        /// <summary>Corner with the smaller X and Y.</summary>
        public readonly Vector2 Min;

        /// <summary>Corner with the larger X and Y.</summary>
        public readonly Vector2 Max;

        /// <summary>Creates a box from its two corners.</summary>
        /// <exception cref="ArgumentException">If <paramref name="max"/> is below <paramref name="min"/> on either axis.</exception>
        public Aabb(Vector2 min, Vector2 max)
        {
            if (max.X < min.X || max.Y < min.Y)
            {
                throw new ArgumentException($"max {max} must not be below min {min}", nameof(max));
            }

            Min = min;
            Max = max;
        }

        /// <summary>Creates a box from a corner and a size.</summary>
        public static Aabb FromSize(Vector2 min, Vector2 size) => new Aabb(min, min + size);

        /// <summary>Creates a box from its centre and full size.</summary>
        public static Aabb FromCenter(Vector2 center, Vector2 size)
        {
            var half = size * 0.5f;
            return new Aabb(center - half, center + half);
        }

        /// <summary>Width and height.</summary>
        public Vector2 Size => Max - Min;

        /// <summary>Half of <see cref="Size"/>.</summary>
        public Vector2 Extents => (Max - Min) * 0.5f;

        /// <summary>The midpoint.</summary>
        public Vector2 Center => (Min + Max) * 0.5f;

        /// <summary>Smaller X edge.</summary>
        public float Left => Min.X;

        /// <summary>Larger X edge.</summary>
        public float Right => Max.X;

        /// <summary>Smaller Y edge.</summary>
        public float Bottom => Min.Y;

        /// <summary>Larger Y edge.</summary>
        public float Top => Max.Y;

        /// <summary>Moves the box by an offset.</summary>
        public Aabb Translated(Vector2 delta) => new Aabb(Min + delta, Max + delta);

        /// <summary>Grows the box by <paramref name="amount"/> on every side.</summary>
        public Aabb Expanded(float amount) =>
            new Aabb(new Vector2(Min.X - amount, Min.Y - amount), new Vector2(Max.X + amount, Max.Y + amount));

        /// <summary>The smallest box containing both.</summary>
        public Aabb Union(Aabb other) =>
            new Aabb(Vector2.Min(Min, other.Min), Vector2.Max(Max, other.Max));

        /// <summary>
        /// The box covering this one's entire path over a step. If this does not
        /// overlap a candidate, no continuous test against it can hit, so it is a
        /// cheap rejection before the real sweep.
        /// </summary>
        public Aabb SweptBounds(Vector2 velocity) => Union(Translated(velocity));

        /// <summary>Whether a point lies inside or on the boundary.</summary>
        public bool Contains(Vector2 point) =>
            point.X >= Min.X && point.X <= Max.X && point.Y >= Min.Y && point.Y <= Max.Y;

        /// <summary>Whether two boxes overlap.</summary>
        /// <param name="other">The box to test against.</param>
        /// <param name="touchingCounts">
        /// Whether exactly flush edges collide. The default of <c>false</c> is what
        /// solids want: an entity standing precisely on a floor touches it every
        /// single frame, and if that registered as a collision the resolver would
        /// fight it forever.
        /// </param>
        public bool Overlaps(Aabb other, bool touchingCounts = false)
        {
            if (touchingCounts)
            {
                return Min.X <= other.Max.X && Max.X >= other.Min.X &&
                       Min.Y <= other.Max.Y && Max.Y >= other.Min.Y;
            }

            return Min.X < other.Max.X && Max.X > other.Min.X &&
                   Min.Y < other.Max.Y && Max.Y > other.Min.Y;
        }

        /// <summary>
        /// The smallest offset that moves this box clear of <paramref name="other"/>,
        /// or <c>null</c> when they are already apart.
        /// </summary>
        /// <remarks>
        /// Overlap depth is measured on both axes and the shallower wins, because
        /// ejecting along the deep axis would teleport the entity across the
        /// obstacle. This is correct for a lone box and subtly wrong for a grid of
        /// them: walking along a tiled floor, the shallowest escape at a tile seam
        /// is sometimes sideways, so the entity trips over flat ground. For tile
        /// maps, resolve the axes separately instead.
        /// </remarks>
        public Vector2? MinimumTranslation(Aabb other)
        {
            if (!Overlaps(other))
            {
                return null;
            }

            var pushRight = other.Max.X - Min.X;
            var pushLeft = other.Min.X - Max.X;
            var pushUp = other.Max.Y - Min.Y;
            var pushDown = other.Min.Y - Max.Y;

            var x = pushRight < -pushLeft ? pushRight : pushLeft;
            var y = pushUp < -pushDown ? pushUp : pushDown;

            return Math.Abs(x) < Math.Abs(y) ? new Vector2(x, 0f) : new Vector2(0f, y);
        }

        /// <summary>The point on or inside this box nearest to <paramref name="point"/>.</summary>
        public Vector2 ClosestPoint(Vector2 point) => Vector2.Clamp(point, Min, Max);

        /// <summary>
        /// Whether a circle intersects this box, via the nearest point on it.
        /// </summary>
        /// <remarks>
        /// The tempting shortcut of expanding the box by the radius is wrong at
        /// outside corners: it makes a square where the reachable region is
        /// rounded, and reports a collision on the diagonal where the real circle
        /// does not reach.
        /// </remarks>
        public bool IntersectsCircle(Vector2 center, float radius) =>
            Vector2.DistanceSquared(center, ClosestPoint(center)) < radius * radius;

        /// <inheritdoc/>
        public bool Equals(Aabb other) => Min.Equals(other.Min) && Max.Equals(other.Max);

        /// <inheritdoc/>
        public override bool Equals(object? obj) => obj is Aabb other && Equals(other);

        /// <inheritdoc/>
        public override int GetHashCode() => unchecked((Min.GetHashCode() * 397) ^ Max.GetHashCode());

        /// <summary>Equality operator.</summary>
        public static bool operator ==(Aabb left, Aabb right) => left.Equals(right);

        /// <summary>Inequality operator.</summary>
        public static bool operator !=(Aabb left, Aabb right) => !left.Equals(right);

        /// <inheritdoc/>
        public override string ToString() => $"Aabb(min={Min}, max={Max})";
    }
}
