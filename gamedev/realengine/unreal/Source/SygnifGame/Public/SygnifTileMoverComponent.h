#pragma once

// Moves an actor through a tile grid using the core's swept collision, with no
// Unreal physics involved.
//
// WHY NOT CHARACTERMOVEMENTCOMPONENT
//
// For most games you should use it, or at least UPawnMovementComponent. This
// exists for the cases where the engine's mover is the wrong tool, and those
// cases are specific:
//
//   * Precise 2D feel. UCharacterMovementComponent is built around a capsule on
//     a navigable 3D world, with its own notions of walkable slope, step-up
//     height and falling. A tight platformer wants instant acceleration, an
//     exact jump arc, and asymmetric gravity going up versus coming down.
//     Fighting the component to get that is harder than not using it.
//
//   * Determinism. Chaos needs a live UWorld, is stateful, and is not
//     bit-reproducible across platforms. Anything backing a replay, a
//     deterministic test, or lockstep networking has to move outside it.
//
// THE TRAP THIS AVOIDS
//
// Unreal's default collision handling on a moving component is discrete: it asks
// "do these overlap now". A fast projectile in front of a thin wall on one tick
// and behind it on the next never overlaps at any sampled instant, so it passes
// straight through. In the engine the fix is bSweepCollision on the move, or CCD
// on the body instance; here the core's swept query asks the continuous question
// directly — over this motion, when did contact first occur.

#include "CoreMinimal.h"
#include "Components/ActorComponent.h"

#include "gamecore/Aabb.h"
#include "gamecore/TileGrid.h"

#include "SygnifTileMoverComponent.generated.h"

UCLASS(ClassGroup = (Sygnif), meta = (BlueprintSpawnableComponent))
class USygnifTileMoverComponent : public UActorComponent
{
	GENERATED_BODY()

public:
	USygnifTileMoverComponent();

	/** Collision box size in world units, centred on the actor. */
	UPROPERTY(EditAnywhere, BlueprintReadOnly, Category = "Sygnif|Movement")
	FVector2D BoxSize = FVector2D(60.0, 120.0);

	/**
	 * Gap kept at contact, in world units.
	 *
	 * Landing exactly flush against a surface means the next step starts touching
	 * it, and floating point then decides at random whether that counts as a
	 * collision, which shows up as the grounded flag flickering on and off while
	 * standing still. Note this is a distance, not a fraction of the step: making
	 * it proportional would leave a fast mover stopping visibly short of the wall
	 * it hit.
	 */
	UPROPERTY(EditAnywhere, BlueprintReadOnly, Category = "Sygnif|Movement",
		meta = (ClampMin = "0.0", ClampMax = "5.0"))
	float SkinWidth = 0.1f;

	/** Whether the last move ended with ground underfoot. */
	UPROPERTY(BlueprintReadOnly, Category = "Sygnif|Movement")
	bool bIsGrounded = false;

	/** Whether the last move struck a wall. */
	UPROPERTY(BlueprintReadOnly, Category = "Sygnif|Movement")
	bool bIsTouchingWall = false;

	/**
	 * Supplies the level to collide against.
	 *
	 * A raw pointer and a plain C++ setter rather than a UPROPERTY, because the
	 * grid is an engine-free type that the reflection system cannot describe.
	 * Ownership sits with whatever loaded the level; this component only reads.
	 */
	void SetGrid(const gamecore::TileGrid* InGrid) { Grid = InGrid; }

	/**
	 * Moves by a displacement in world units, sliding along contacts, and returns
	 * the motion actually achieved.
	 *
	 * Takes a displacement rather than a velocity so this component never needs an
	 * opinion about what the timestep is — that belongs to the fixed-step
	 * component driving it.
	 */
	FVector2D MoveBy(const FVector2D& Displacement);

	/** The mover's collision bounds at its current location. */
	gamecore::Aabb GetBounds() const;

private:
	const gamecore::TileGrid* Grid = nullptr;

	// Reused across frames so a moving actor does not allocate per tick.
	// Per-frame allocation is the most common avoidable performance defect in
	// gameplay code, and it surfaces as periodic hitching rather than as a
	// steady cost, which makes it disproportionately hard to attribute.
	std::vector<gamecore::Aabb> ObstacleBuffer;
};
