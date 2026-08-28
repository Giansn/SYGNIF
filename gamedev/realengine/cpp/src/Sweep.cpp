#include "gamecore/Sweep.h"

#include <limits>

namespace gamecore
{

bool SweepAgainst(const Aabb& Mover, const Vec2& Velocity, const Aabb& Target, SweepHit& OutHit)
{
    float XEntryDistance, XExitDistance, YEntryDistance, YExitDistance;

    if (Velocity.X > 0.0f)
    {
        XEntryDistance = Target.Min.X - Mover.Max.X;
        XExitDistance = Target.Max.X - Mover.Min.X;
    }
    else
    {
        XEntryDistance = Target.Max.X - Mover.Min.X;
        XExitDistance = Target.Min.X - Mover.Max.X;
    }

    if (Velocity.Y > 0.0f)
    {
        YEntryDistance = Target.Min.Y - Mover.Max.Y;
        YExitDistance = Target.Max.Y - Mover.Min.Y;
    }
    else
    {
        YEntryDistance = Target.Max.Y - Mover.Min.Y;
        YExitDistance = Target.Min.Y - Mover.Max.Y;
    }

    constexpr float Infinity = std::numeric_limits<float>::infinity();
    float XEntry, XExit, YEntry, YExit;

    // A zero-velocity axis never enters or leaves its slab. If the boxes already
    // overlap on that axis the constraint holds for all time; if not, contact is
    // impossible. Dividing by zero here instead produces a NaN that silently
    // poisons the whole physics step — and because NaN compares false against
    // everything, the collision simply stops happening with no error anywhere.
    if (IsExactlyZero(Velocity.X))
    {
        if (Mover.Max.X <= Target.Min.X || Mover.Min.X >= Target.Max.X)
        {
            return false;
        }
        XEntry = -Infinity;
        XExit = Infinity;
    }
    else
    {
        XEntry = XEntryDistance / Velocity.X;
        XExit = XExitDistance / Velocity.X;
    }

    if (IsExactlyZero(Velocity.Y))
    {
        if (Mover.Max.Y <= Target.Min.Y || Mover.Min.Y >= Target.Max.Y)
        {
            return false;
        }
        YEntry = -Infinity;
        YExit = Infinity;
    }
    else
    {
        YEntry = YEntryDistance / Velocity.Y;
        YExit = YExitDistance / Velocity.Y;
    }

    const float EntryTime = XEntry > YEntry ? XEntry : YEntry;
    const float ExitTime = XExit < YExit ? XExit : YExit;

    if (EntryTime > ExitTime || EntryTime > 1.0f || ExitTime <= 0.0f)
    {
        return false;
    }

    if (XEntry > 1.0f || YEntry > 1.0f)
    {
        return false;
    }

    // Starting already overlapped reports immediate contact rather than
    // pretending nothing is wrong, so the caller can push out.
    OutHit.Time = EntryTime < 0.0f ? 0.0f : EntryTime;

    // Whichever axis was entered last is the face that was struck.
    OutHit.Normal = XEntry > YEntry
        ? Vec2(Velocity.X > 0.0f ? -1.0f : 1.0f, 0.0f)
        : Vec2(0.0f, Velocity.Y > 0.0f ? -1.0f : 1.0f);
    OutHit.Index = -1;
    return true;
}

bool SweepAgainstAll(const Aabb& Mover, const Vec2& Velocity, const Aabb* Targets, size_t Count, SweepHit& OutHit)
{
    if (Targets == nullptr || Count == 0)
    {
        return false;
    }

    bool bFound = false;
    SweepHit Best;

    for (size_t i = 0; i < Count; ++i)
    {
        SweepHit Candidate;
        if (SweepAgainst(Mover, Velocity, Targets[i], Candidate))
        {
            if (!bFound || Candidate.Time < Best.Time)
            {
                Candidate.Index = static_cast<int>(i);
                Best = Candidate;
                bFound = true;
            }
        }
    }

    if (bFound)
    {
        OutHit = Best;
    }
    return bFound;
}

Vec2 MoveAndSlide(
    const Aabb& Box,
    const Vec2& Velocity,
    const Aabb* Obstacles,
    size_t ObstacleCount,
    SweepHit* OutHits,
    int* OutHitCount,
    int MaxIterations,
    float Skin)
{
    Vec2 Remaining = Velocity;
    Vec2 Total = Vec2::Zero();
    Aabb Current = Box;
    int HitCount = 0;

    for (int Iteration = 0; Iteration < MaxIterations; ++Iteration)
    {
        const float Speed = Remaining.Length();
        if (IsExactlyZero(Speed))
        {
            break;
        }

        SweepHit Hit;
        if (!SweepAgainstAll(Current, Remaining, Obstacles, ObstacleCount, Hit))
        {
            Total += Remaining;
            break;
        }

        // Back off by a fixed distance, converted into this step's time units.
        float SafeTime = Hit.Time - (Skin / Speed);
        if (SafeTime < 0.0f)
        {
            SafeTime = 0.0f;
        }

        const Vec2 Step = Remaining * SafeTime;
        Total += Step;
        Current = Current.Translated(Step);

        if (OutHits != nullptr && HitCount < MaxIterations)
        {
            OutHits[HitCount] = Hit;
        }
        ++HitCount;

        // Project the leftover motion onto the surface: drop the component
        // heading into it, keep the component running along it.
        const Vec2 Leftover = Remaining * (1.0f - SafeTime);
        Remaining = Leftover - Hit.Normal * Leftover.Dot(Hit.Normal);
    }

    if (OutHitCount != nullptr)
    {
        *OutHitCount = HitCount;
    }

    return Total;
}

} // namespace gamecore
