#pragma once

// A fixed simulation timestep for Unreal gameplay.
//
// WHY THIS COMPONENT EXISTS AT ALL
//
// This is the single largest scheduling difference between Unreal and Unity, and
// it is easy to miss because both engines *look* like they tick the same way.
//
// Unity gives you FixedUpdate: an accumulator over Time.fixedDeltaTime, clamped
// by Time.maximumDeltaTime. Deterministic gameplay goes there and the engine
// handles the rest.
//
// Unreal has no gameplay equivalent. AActor::Tick and UActorComponent::
// TickComponent receive the raw frame delta. The fixed-step machinery that does
// exist belongs to the physics solver — Project Settings > Physics >
// Substepping, with MaxSubstepDeltaTime — and steps Chaos, not your code.
// UEngine::bUseFixedFrameRate exists but pins the whole engine to a fixed rate,
// which is a tool for rendering cinematics deterministically, not for gameplay:
// it makes the game run in slow motion the moment a frame takes too long.
//
// So anything in Unreal that must be reproducible — replays, deterministic
// tests, lockstep or rollback multiplayer, or simply a jump that reaches the
// same height on a 30 fps machine and a 240 fps one — needs its own accumulator.
// That is all this component is: the same algorithm Unity runs for you,
// implemented over TickComponent, delegating to the tested core.
//
// The failure it prevents is worth stating concretely. Integrating gravity with
// a variable delta accumulates differently over one 32 ms step than over two
// 16 ms steps, so jump height genuinely changes with frame rate. A player on a
// slow machine cannot clear a gap that a fast machine clears, and the bug
// reproduces only on hardware you do not have.
//
// TICK ORDER
//
// This component sets its tick group to TG_PrePhysics and a high tick priority
// so the simulation advances before anything reads its results in the same
// frame. Leaving tick order to chance is a real bug class in Unreal: it works
// until an unrelated actor is added and the ordering silently changes.

#include "CoreMinimal.h"
#include "Components/ActorComponent.h"

#include "gamecore/FixedTimestep.h"

#include "SygnifFixedStepComponent.generated.h"

/**
 * Fires once per fixed simulation step, with a constant delta.
 *
 * A multicast delegate rather than a virtual: several systems on the same actor
 * usually want the same tick, and the alternative is a base class everything has
 * to inherit from.
 */
DECLARE_DYNAMIC_MULTICAST_DELEGATE_OneParam(FSygnifFixedStep, float, FixedDeltaSeconds);

UCLASS(ClassGroup = (Sygnif), meta = (BlueprintSpawnableComponent))
class USygnifFixedStepComponent : public UActorComponent
{
	GENERATED_BODY()

public:
	USygnifFixedStepComponent();

	/** Broadcast once per fixed step. May fire zero or several times per frame. */
	UPROPERTY(BlueprintAssignable, Category = "Sygnif|Simulation")
	FSygnifFixedStep OnFixedStep;

	/**
	 * Simulation rate. 60 is a sane default; lower it for a cheaper simulation,
	 * raise it for fast projectiles that need finer collision resolution.
	 */
	UPROPERTY(EditAnywhere, BlueprintReadOnly, Category = "Sygnif|Simulation",
		meta = (ClampMin = "10.0", ClampMax = "240.0"))
	float TicksPerSecond = 60.0f;

	/**
	 * Longest frame time honoured, in seconds. Anything beyond this is discarded.
	 *
	 * This is the spiral-of-death guard. If a tick costs more to compute than the
	 * time it represents, each frame queues more ticks than the last and the game
	 * locks up. Discarding the excess trades a briefly slow-motion simulation for
	 * staying responsive, which is the right trade: a game at half speed is
	 * playable, a frozen one is not. Unity's equivalent is Time.maximumDeltaTime,
	 * which defaults to 0.333.
	 */
	UPROPERTY(EditAnywhere, BlueprintReadOnly, Category = "Sygnif|Simulation",
		meta = (ClampMin = "0.05", ClampMax = "1.0"))
	float MaxFrameSeconds = 0.25f;

	/**
	 * Leftover fraction of a step, in [0, 1).
	 *
	 * Rendering should interpolate between the previous and current simulation
	 * states by this amount. Without it, a 60 Hz simulation displayed at 144 Hz
	 * shows each simulated position for two or three frames and then jumps, which
	 * reads as juddery motion even though the simulation is perfectly smooth.
	 */
	UFUNCTION(BlueprintPure, Category = "Sygnif|Simulation")
	float GetInterpolationAlpha() const;

	/** Fixed delta in seconds, the value passed to OnFixedStep. */
	UFUNCTION(BlueprintPure, Category = "Sygnif|Simulation")
	float GetFixedDeltaSeconds() const { return 1.0f / FMath::Max(TicksPerSecond, 1.0f); }

	/** Total steps run since play began. Useful as a replay/lockstep frame number. */
	UFUNCTION(BlueprintPure, Category = "Sygnif|Simulation")
	int32 GetStepCount() const { return StepCount; }

	/**
	 * Real time discarded by the frame-time clamp.
	 *
	 * Non-zero means the simulation could not keep up. It is a performance bug,
	 * not a cosmetic one, and is exposed rather than hidden so it can be surfaced
	 * on a debug HUD instead of being discovered by a player.
	 */
	UFUNCTION(BlueprintPure, Category = "Sygnif|Simulation")
	float GetDroppedSeconds() const;

	/** Discards banked time. Call after a load screen or an unpause. */
	UFUNCTION(BlueprintCallable, Category = "Sygnif|Simulation")
	void ResetAccumulator();

	//~ Begin UActorComponent interface
	virtual void BeginPlay() override;
	virtual void TickComponent(
		float DeltaTime,
		ELevelTick TickType,
		FActorComponentTickFunction* ThisTickFunction) override;
	//~ End UActorComponent interface

private:
	/**
	 * The accumulator itself, from the engine-free core.
	 *
	 * A TUniquePtr rather than a by-value member because the core type is a plain
	 * C++ class with a constructor that takes arguments, and UObject construction
	 * does not run member constructors with parameters at the point the settings
	 * above are known. Building it in BeginPlay, once the UPROPERTY values are
	 * final, keeps the configuration in one place.
	 */
	TUniquePtr<gamecore::FixedTimestepAccumulator> Accumulator;

	int32 StepCount = 0;
};
