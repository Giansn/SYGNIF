#include "SygnifFixedStepComponent.h"

USygnifFixedStepComponent::USygnifFixedStepComponent()
{
	PrimaryComponentTick.bCanEverTick = true;

	// TG_PrePhysics so the simulation advances before physics and before
	// anything reading its results in the same frame. Leaving tick order to
	// chance works until an unrelated actor is added and the ordering silently
	// changes, which is a genuinely miserable bug to find.
	PrimaryComponentTick.TickGroup = TG_PrePhysics;

	// Nothing here needs to run before the game starts.
	bAutoActivate = true;
}

void USygnifFixedStepComponent::BeginPlay()
{
	Super::BeginPlay();

	const float SafeRate = FMath::Max(TicksPerSecond, 1.0f);
	const double FixedDelta = 1.0 / static_cast<double>(SafeRate);

	// The clamp must be at least one step, or the accumulator can never produce
	// a tick. Raising it rather than rejecting it keeps a misconfigured value in
	// the editor from silently freezing the simulation.
	const double MaxFrame = FMath::Max(static_cast<double>(MaxFrameSeconds), FixedDelta);

	Accumulator = MakeUnique<gamecore::FixedTimestepAccumulator>(FixedDelta, MaxFrame);
	StepCount = 0;
}

void USygnifFixedStepComponent::TickComponent(
	float DeltaTime,
	ELevelTick TickType,
	FActorComponentTickFunction* ThisTickFunction)
{
	Super::TickComponent(DeltaTime, TickType, ThisTickFunction);

	if (!Accumulator.IsValid())
	{
		return;
	}

	const int Steps = Accumulator->Feed(static_cast<double>(DeltaTime));
	const float FixedDelta = static_cast<float>(Accumulator->GetDeltaTime());

	for (int Step = 0; Step < Steps; ++Step)
	{
		++StepCount;
		OnFixedStep.Broadcast(FixedDelta);

		// A listener can destroy this actor mid-step — a player dying to a hazard
		// during the simulation is the ordinary case, not an edge case. Bailing
		// out here avoids broadcasting into a half-torn-down actor, which in
		// Unreal surfaces as an access violation rather than an exception.
		if (!IsValid(this) || !IsValid(GetOwner()))
		{
			return;
		}
	}
}

float USygnifFixedStepComponent::GetInterpolationAlpha() const
{
	return Accumulator.IsValid() ? static_cast<float>(Accumulator->Alpha()) : 0.0f;
}

float USygnifFixedStepComponent::GetDroppedSeconds() const
{
	return Accumulator.IsValid() ? static_cast<float>(Accumulator->GetDroppedTime()) : 0.0f;
}

void USygnifFixedStepComponent::ResetAccumulator()
{
	if (Accumulator.IsValid())
	{
		Accumulator->Reset();
	}
}
