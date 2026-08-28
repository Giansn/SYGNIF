#pragma once

// Conversions between Unreal's math types and the engine-free core's.
//
// Two vector types in one codebase looks like a wart, and the temptation is to
// delete gamecore::Vec2 and use FVector2D everywhere. Doing that is what makes
// gameplay logic untestable: FVector2D lives in Core, so any file mentioning it
// can only be compiled by UnrealBuildTool, which means the only way to run a
// test over it is to build the engine. Keeping the boundary costs four inline
// functions and buys a test suite that runs in milliseconds.
//
// AXIS CONVENTION -- the part that actually bites.
//
// Unreal is Z-up and left-handed, with X forward, Y right, Z up. That is not a
// detail you can defer. A side-scroller in Unreal conventionally plays on the
// XZ plane (X sideways, Z up) with Y as depth, while a top-down game plays on
// XY. The core has no opinion: it calls the axes X and Y and this header decides
// which world axes those are, in exactly one place.
//
// Getting this wrong is not a compile error. It produces a game where gravity
// pulls sideways or the character moves into the screen, and it is debugged by
// staring at numbers, so it is worth being explicit about even though it looks
// like ceremony.

#include "CoreMinimal.h"

#include "gamecore/Aabb.h"
#include "gamecore/Vec2.h"

namespace SygnifCore
{
	/** The world plane the 2D gameplay core is mapped onto. */
	enum class EGameplayPlane : uint8
	{
		/** Top-down: core X is world X, core Y is world Y. */
		XY,

		/** Side-on: core X is world X, core Y is world Z (up). */
		XZ,
	};

	/** The plane this project plays on. Change here, nowhere else. */
	inline constexpr EGameplayPlane GameplayPlane = EGameplayPlane::XZ;

	/** Core vector to a full world vector, on the configured plane. */
	FORCEINLINE FVector ToWorld(const gamecore::Vec2& Value, float Depth = 0.0f)
	{
		return GameplayPlane == EGameplayPlane::XZ
			? FVector(Value.X, Depth, Value.Y)
			: FVector(Value.X, Value.Y, Depth);
	}

	/** World vector to core vector, discarding the depth axis. */
	FORCEINLINE gamecore::Vec2 ToCore(const FVector& Value)
	{
		return GameplayPlane == EGameplayPlane::XZ
			? gamecore::Vec2(static_cast<float>(Value.X), static_cast<float>(Value.Z))
			: gamecore::Vec2(static_cast<float>(Value.X), static_cast<float>(Value.Y));
	}

	/** Core vector to Unreal's 2D vector, for UI and other plane-free uses. */
	FORCEINLINE FVector2D ToVector2D(const gamecore::Vec2& Value)
	{
		return FVector2D(Value.X, Value.Y);
	}

	/** Unreal's 2D vector to the core's. */
	FORCEINLINE gamecore::Vec2 ToCore(const FVector2D& Value)
	{
		return gamecore::Vec2(static_cast<float>(Value.X), static_cast<float>(Value.Y));
	}

	/** Core box to an Unreal box on the configured plane, for debug drawing. */
	FORCEINLINE FBox ToWorldBox(const gamecore::Aabb& Box, float Depth = 0.0f, float Thickness = 1.0f)
	{
		const FVector MinPoint = ToWorld(Box.Min, Depth - Thickness * 0.5f);
		const FVector MaxPoint = ToWorld(Box.Max, Depth + Thickness * 0.5f);
		return FBox(
			FVector(FMath::Min(MinPoint.X, MaxPoint.X), FMath::Min(MinPoint.Y, MaxPoint.Y), FMath::Min(MinPoint.Z, MaxPoint.Z)),
			FVector(FMath::Max(MinPoint.X, MaxPoint.X), FMath::Max(MinPoint.Y, MaxPoint.Y), FMath::Max(MinPoint.Z, MaxPoint.Z)));
	}
}
