#include "SygnifTileMoverComponent.h"

#include "SygnifCoreTypes.h"
#include "gamecore/Sweep.h"

USygnifTileMoverComponent::USygnifTileMoverComponent()
{
	// Movement is driven by the fixed-step component, not by the frame tick, so
	// this component does not tick at all. Ticking here as well would advance
	// movement at the render rate and reintroduce exactly the frame-rate
	// dependence the fixed step exists to remove.
	PrimaryComponentTick.bCanEverTick = false;
}

gamecore::Aabb USygnifTileMoverComponent::GetBounds() const
{
	const AActor* Owner = GetOwner();
	const gamecore::Vec2 Center = Owner != nullptr
		? SygnifCore::ToCore(Owner->GetActorLocation())
		: gamecore::Vec2(0.0f, 0.0f);

	return gamecore::Aabb::FromCenter(
		Center,
		gamecore::Vec2(static_cast<float>(BoxSize.X), static_cast<float>(BoxSize.Y)));
}

FVector2D USygnifTileMoverComponent::MoveBy(const FVector2D& Displacement)
{
	bIsGrounded = false;
	bIsTouchingWall = false;

	AActor* Owner = GetOwner();
	if (Owner == nullptr)
	{
		return FVector2D(0.0, 0.0);
	}

	const gamecore::Vec2 Motion(
		static_cast<float>(Displacement.X),
		static_cast<float>(Displacement.Y));

	if (Grid == nullptr)
	{
		// No level loaded yet: move freely rather than refusing to move, so a
		// missing grid is visible as "walks through everything" rather than as a
		// character frozen in place for no apparent reason.
		Owner->SetActorLocation(Owner->GetActorLocation() + SygnifCore::ToWorld(Motion));
		return Displacement;
	}

	const gamecore::Aabb Box = GetBounds();

	// Broadphase over the swept bounds, not the current bounds. Querying only
	// where the mover is now would miss tiles it flies over within a single
	// step, which is the same tunnelling failure in a different disguise.
	ObstacleBuffer.clear();
	Grid->BoxesOverlapping(Box.SweptBounds(Motion), gamecore::Tile::Solid, ObstacleBuffer);

	gamecore::SweepHit Hits[4];
	int HitCount = 0;

	const gamecore::Vec2 Moved = gamecore::MoveAndSlide(
		Box,
		Motion,
		ObstacleBuffer.empty() ? nullptr : ObstacleBuffer.data(),
		ObstacleBuffer.size(),
		Hits,
		&HitCount,
		4,
		SkinWidth);

	for (int Index = 0; Index < HitCount && Index < 4; ++Index)
	{
		const gamecore::Vec2& Normal = Hits[Index].Normal;

		// The core's Y is up, whichever world axis SygnifCoreTypes maps it to, so
		// a normal pointing +Y means the surface is beneath us.
		if (Normal.Y > 0.5f)
		{
			bIsGrounded = true;
		}
		else if (FMath::Abs(Normal.X) > 0.5f)
		{
			bIsTouchingWall = true;
		}
	}

	// One location write at the end. Writing inside the resolution loop would
	// cost several scene-graph updates per step and, with attached components,
	// can be visible as jitter.
	Owner->SetActorLocation(Owner->GetActorLocation() + SygnifCore::ToWorld(Moved));

	return FVector2D(Moved.X, Moved.Y);
}
