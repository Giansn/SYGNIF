#pragma once

// Stand-in for the UnrealHeaderTool output for SygnifTileMoverComponent.
// See SygnifFixedStepComponent.generated.h for why the Super typedef lives here.

#include "Components/ActorComponent.h"

#undef GENERATED_BODY
#define GENERATED_BODY(...) \
public:                     \
	typedef UActorComponent Super;
