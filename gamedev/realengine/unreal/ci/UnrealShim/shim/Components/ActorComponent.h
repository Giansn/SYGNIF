#pragma once

// Shim for UActorComponent and the tick plumbing. See CoreMinimal.h for what
// this does and does not prove.

#include "CoreMinimal.h"

/** Unreal's tick groups, in their real execution order. */
enum ETickingGroup : uint8
{
	TG_PrePhysics = 0,
	TG_StartPhysics,
	TG_DuringPhysics,
	TG_EndPhysics,
	TG_PostPhysics,
	TG_PostUpdateWork,
	TG_LastDemotable,
	TG_NewlySpawned,
};

enum ELevelTick : uint8
{
	LEVELTICK_TimeOnly = 0,
	LEVELTICK_ViewportsOnly,
	LEVELTICK_All,
	LEVELTICK_PauseTick,
};

struct FActorComponentTickFunction
{
	bool bCanEverTick = false;
	ETickingGroup TickGroup = TG_PrePhysics;
	float TickInterval = 0.0f;
};

class UActorComponent : public UObject
{
public:
	FActorComponentTickFunction PrimaryComponentTick;
	bool bAutoActivate = true;

	virtual void BeginPlay() {}

	virtual void TickComponent(
		float DeltaTime,
		ELevelTick TickType,
		FActorComponentTickFunction* ThisTickFunction)
	{
		(void)DeltaTime;
		(void)TickType;
		(void)ThisTickFunction;
	}

	AActor* GetOwner() const { return OwnerPrivate; }

private:
	// Named as the engine names it. A shim member called "Owner" would make
	// -Wshadow fire on any local called Owner in module code -- a warning that
	// would never appear in a real Unreal build, i.e. the shim inventing a
	// problem instead of reflecting one.
	AActor* OwnerPrivate = nullptr;
};
