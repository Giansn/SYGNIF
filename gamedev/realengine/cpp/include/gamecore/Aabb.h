#pragma once

// Aabb — an axis-aligned bounding box stored corner to corner.
//
// Stored as Min/Max rather than position-plus-size because that is convention
// neutral. The Python original used a top-left corner and a size because it drew
// straight into a framebuffer where Y grows downward; Unreal is Z-up and Unity
// 2D is Y-up, so "top-left plus size" does not survive the port — the meaning of
// every sign flips. Min is simply the smaller corner on both axes, which is true
// in any convention.
//
// Everything here is a plain value type with no virtuals, so it costs nothing to
// pass around and works inside an Unreal USTRUCT without ceremony.

#include <cmath>

#include "gamecore/Vec2.h"

namespace gamecore
{

struct Aabb
{
    Vec2 Min;
    Vec2 Max;

    constexpr Aabb() = default;
    constexpr Aabb(const Vec2& InMin, const Vec2& InMax) : Min(InMin), Max(InMax) {}

    static constexpr Aabb FromSize(const Vec2& InMin, const Vec2& Size)
    {
        return Aabb(InMin, InMin + Size);
    }

    static constexpr Aabb FromCenter(const Vec2& Center, const Vec2& Size)
    {
        const Vec2 Half = Size * 0.5f;
        return Aabb(Center - Half, Center + Half);
    }

    constexpr Vec2 Size() const { return Max - Min; }
    constexpr Vec2 Extents() const { return (Max - Min) * 0.5f; }
    constexpr Vec2 Center() const { return (Min + Max) * 0.5f; }

    constexpr float Left() const { return Min.X; }
    constexpr float Right() const { return Max.X; }
    constexpr float Bottom() const { return Min.Y; }
    constexpr float Top() const { return Max.Y; }

    constexpr Aabb Translated(const Vec2& Delta) const { return Aabb(Min + Delta, Max + Delta); }

    constexpr Aabb Expanded(float Amount) const
    {
        return Aabb(Vec2(Min.X - Amount, Min.Y - Amount), Vec2(Max.X + Amount, Max.Y + Amount));
    }

    constexpr Aabb Union(const Aabb& Other) const
    {
        return Aabb(gamecore::Min(Min, Other.Min), gamecore::Max(Max, Other.Max));
    }

    // The box covering this one's entire path over a step. A cheap rejection: if
    // this does not overlap a candidate, no continuous test against it can hit.
    constexpr Aabb SweptBounds(const Vec2& Velocity) const
    {
        return Union(Translated(Velocity));
    }

    constexpr bool Contains(const Vec2& Point) const
    {
        return Point.X >= Min.X && Point.X <= Max.X && Point.Y >= Min.Y && Point.Y <= Max.Y;
    }

    // `bTouchingCounts` decides whether exactly flush edges collide. The default
    // of false is what solids want: an entity standing precisely on a floor
    // touches it every single frame, and if that registered as a collision the
    // resolver would fight it forever.
    constexpr bool Overlaps(const Aabb& Other, bool bTouchingCounts = false) const
    {
        if (bTouchingCounts)
        {
            return Min.X <= Other.Max.X && Max.X >= Other.Min.X &&
                   Min.Y <= Other.Max.Y && Max.Y >= Other.Min.Y;
        }

        return Min.X < Other.Max.X && Max.X > Other.Min.X &&
               Min.Y < Other.Max.Y && Max.Y > Other.Min.Y;
    }

    // The smallest offset that moves this box clear of Other. Returns false when
    // they are already apart.
    //
    // Overlap depth is measured on both axes and the shallower wins, because
    // ejecting along the deep axis would teleport the entity across the obstacle.
    // Correct for a lone box, subtly wrong for a grid of them: walking a tiled
    // floor, the shallowest escape at a tile seam is sometimes sideways, so the
    // entity trips over flat ground. For tile maps, resolve the axes separately.
    bool MinimumTranslation(const Aabb& Other, Vec2& OutPush) const
    {
        if (!Overlaps(Other))
        {
            return false;
        }

        const float PushRight = Other.Max.X - Min.X;
        const float PushLeft = Other.Min.X - Max.X;
        const float PushUp = Other.Max.Y - Min.Y;
        const float PushDown = Other.Min.Y - Max.Y;

        const float X = PushRight < -PushLeft ? PushRight : PushLeft;
        const float Y = PushUp < -PushDown ? PushUp : PushDown;

        OutPush = std::fabs(X) < std::fabs(Y) ? Vec2(X, 0.0f) : Vec2(0.0f, Y);
        return true;
    }

    constexpr Vec2 ClosestPoint(const Vec2& Point) const
    {
        return Vec2(Clamp(Point.X, Min.X, Max.X), Clamp(Point.Y, Min.Y, Max.Y));
    }

    // Circle against box, via the nearest point on the box.
    //
    // The tempting shortcut of expanding the box by the radius is wrong at
    // outside corners: it makes a square where the reachable region is rounded,
    // and reports a hit on the diagonal where the real circle does not reach.
    constexpr bool IntersectsCircle(const Vec2& Center, float Radius) const
    {
        return DistanceSquared(Center, ClosestPoint(Center)) < Radius * Radius;
    }

    constexpr bool operator==(const Aabb& Other) const { return Min == Other.Min && Max == Other.Max; }
    constexpr bool operator!=(const Aabb& Other) const { return !(*this == Other); }
};

} // namespace gamecore
