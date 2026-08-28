#pragma once

// Stand-in for the UnrealHeaderTool output for SygnifFixedStepComponent.
//
// UHT generates, per class, the reflection tables, the Super typedef and the
// constructor plumbing that GENERATED_BODY() expands to. Only the reflection
// tables need the engine; the Super typedef does not, and module code uses it
// constantly (Super::BeginPlay, Super::TickComponent), so the shim supplies it
// here rather than through one global macro that cannot know each class's base.

#include "Components/ActorComponent.h"

#undef GENERATED_BODY
#define GENERATED_BODY(...) \
public:                     \
	typedef UActorComponent Super;
