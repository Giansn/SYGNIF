#pragma once

// Continuous collision detection and response for axis-aligned boxes.
//
// Unreal ships this and in production you should normally use it —
// UWorld::SweepSingleByChannel, and UCharacterMovementComponent for anything
// that walks. Implementing it here is still worth the effort for two reasons
// that survive that fact.
//
// First, understanding it is what lets you configure the engine's version
// correctly. Every project eventually files the same bug: a fast projectile
// passes through a thin wall. The cause is discrete detection asking "do these
// overlap right now" — a question a bullet that was in front on one tick and
// behind on the next never answers yes to. Knowing that turns a mystery into a
// one-setting fix (bSweepCollision on the movement component, or CCD on the
// body instance).
//
// Second, anything that must be deterministic and unit-testable cannot call into
// the engine's physics: Chaos needs a live UWorld, is stateful, and is not
// bit-reproducible across platforms. Replay validation and gameplay tests have
// to live in code like this.
//
// No exceptions and no RTTI: this compiles under -fno-exceptions -fno-rtti,
// matching an Unreal game module's default build settings.

#include <cstddef>

#include "gamecore/Aabb.h"
#include "gamecore/Vec2.h"

namespace gamecore
{

struct SweepHit
{
    // Fraction of the attempted motion completed before contact, in [0, 1]. So
    // Start + Velocity * Time is where the mover should stop.
    float Time = 0.0f;

    // Unit vector pointing out of the surface that was struck.
    Vec2 Normal;

    // Index of the obstacle hit when the query was against an array, else -1.
    int Index = -1;
};

// Finds when, during a motion, Mover first touches Target. Returns false if
// there is no contact within this step.
//
// The slab method: treat the target as two slabs, one per axis, and for each
// compute when the mover enters and leaves it. The boxes overlap during the
// window where both slabs are entered, so contact begins at
// max(EntryX, EntryY) and ends at min(ExitX, ExitY). If the window starts after
// it ends, the mover passes by without touching.
bool SweepAgainst(const Aabb& Mover, const Vec2& Velocity, const Aabb& Target, SweepHit& OutHit);

// Earliest contact against any of Targets. Only the first hit matters:
// everything after it lies on a trajectory the mover never actually takes.
bool SweepAgainstAll(const Aabb& Mover, const Vec2& Velocity, const Aabb* Targets, size_t Count, SweepHit& OutHit);

// Moves as far as possible along Velocity, sliding along whatever is struck,
// and returns the motion actually performed.
//
// Stopping dead on contact makes a game feel like it is made of glue: brush a
// wall while running diagonally and you halt instead of continuing along it.
// Sliding keeps the component of motion parallel to the surface and discards
// only the component heading into it.
//
// `MaxIterations` caps the resolution passes. Sliding off one surface can push
// into another — an inside corner needs two in a single step — and the cap stops
// a wedge-shaped dead end from looping forever.
//
// `Skin` is the gap left at contact, in world units and NOT as a fraction of the
// step. Landing exactly on a surface means the next step starts flush against
// it, and floating point then decides at random whether that counts as touching,
// which shows up as an entity flickering between grounded and airborne.
// Expressing it as a fraction is a trap: the standoff would scale with speed, so
// a bullet stops visibly short of the wall it hit while a slow walker is left
// touching it.
//
// OutHits may be null; when supplied it receives up to MaxIterations contacts and
// OutHitCount is set to how many were written.
Vec2 MoveAndSlide(
    const Aabb& Box,
    const Vec2& Velocity,
    const Aabb* Obstacles,
    size_t ObstacleCount,
    SweepHit* OutHits = nullptr,
    int* OutHitCount = nullptr,
    int MaxIterations = 4,
    float Skin = 1e-4f);

} // namespace gamecore
