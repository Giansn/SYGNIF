#pragma once

// A minimal stand-in for the parts of Unreal's Core the gameplay module touches.
//
// WHAT THIS IS FOR
// Unreal cannot be installed in this environment: a source build is tens of
// gigabytes and hours of compilation. That would normally mean the gameplay
// components are unverifiable — written, committed, and only discovered to be
// wrong when someone with the engine tries to build them.
//
// Compiling them against a shim closes most of that gap. It proves the code
// parses, that every type and member it references exists with the assumed
// signature, that overload resolution works, and that the engine-free core
// genuinely links against engine-facing code. It also proves the module code
// itself is exception-free and RTTI-free, because this is built with
// -fno-exceptions -fno-rtti exactly as an Unreal game module is.
//
// WHAT THIS IS NOT
// It proves nothing about the engine. In particular it does NOT verify anything
// touching UnrealHeaderTool: reflection, property serialisation, Blueprint
// exposure, replication, or garbage collection. UCLASS, UPROPERTY and UFUNCTION
// are erased to nothing here, so a mistake that only UHT would catch — an
// unsupported UPROPERTY type, a bad meta specifier, a delegate signature
// mismatch — passes silently. Treat a green build as "this is valid C++ that
// will compile", not "this works in Unreal".
//
// The shim mirrors Unreal's real signatures and naming, including FMath, the
// F/U/A type prefixes and TUniquePtr's IsValid(), because those are precisely
// the details module code gets wrong.

#include <cmath>
#include <cstdint>
#include <utility>

// -- primitive type aliases -------------------------------------------------

using int8 = std::int8_t;
using int16 = std::int16_t;
using int32 = std::int32_t;
using int64 = std::int64_t;
using uint8 = std::uint8_t;
using uint16 = std::uint16_t;
using uint32 = std::uint32_t;
using uint64 = std::uint64_t;

#define FORCEINLINE inline
#define UPROPERTY(...)
#define UFUNCTION(...)
#define UCLASS(...)
#define USTRUCT(...)
#define UENUM(...)
#define UMETA(...)
#define GENERATED_BODY(...) public:
#define GENERATED_USTRUCT_BODY(...) public:

// UHT generates one of these per delegate. The shim only needs the call shape.
#define DECLARE_DYNAMIC_MULTICAST_DELEGATE_OneParam(DelegateName, Param1Type, Param1Name) \
	struct DelegateName                                                                   \
	{                                                                                     \
		void Broadcast(Param1Type Param1Name) { (void)Param1Name; }                       \
	}

#define DECLARE_DYNAMIC_MULTICAST_DELEGATE(DelegateName) \
	struct DelegateName                                  \
	{                                                    \
		void Broadcast() {}                              \
	}

// -- math -------------------------------------------------------------------

struct FVector
{
	double X = 0.0;
	double Y = 0.0;
	double Z = 0.0;

	FVector() = default;
	FVector(double InX, double InY, double InZ) : X(InX), Y(InY), Z(InZ) {}

	FVector operator+(const FVector& Other) const { return FVector(X + Other.X, Y + Other.Y, Z + Other.Z); }
	FVector operator-(const FVector& Other) const { return FVector(X - Other.X, Y - Other.Y, Z - Other.Z); }
	FVector operator*(double Scale) const { return FVector(X * Scale, Y * Scale, Z * Scale); }
	FVector& operator+=(const FVector& Other) { X += Other.X; Y += Other.Y; Z += Other.Z; return *this; }
};

struct FVector2D
{
	double X = 0.0;
	double Y = 0.0;

	FVector2D() = default;
	FVector2D(double InX, double InY) : X(InX), Y(InY) {}
};

struct FBox
{
	FVector Min;
	FVector Max;

	FBox() = default;
	FBox(const FVector& InMin, const FVector& InMax) : Min(InMin), Max(InMax) {}
};

struct FMath
{
	template <typename T>
	static T Max(T A, T B) { return A > B ? A : B; }

	template <typename T>
	static T Min(T A, T B) { return A < B ? A : B; }

	template <typename T>
	static T Abs(T A) { return A < T(0) ? -A : A; }

	template <typename T>
	static T Clamp(T Value, T Low, T High) { return Value < Low ? Low : (Value > High ? High : Value); }
};

// -- smart pointers ---------------------------------------------------------

// Unreal's own unique pointer, reproduced because module code calls IsValid()
// on it, which std::unique_ptr does not have. Getting that wrong is exactly the
// kind of mistake this shim is here to catch.
template <typename T>
class TUniquePtr
{
public:
	TUniquePtr() = default;
	explicit TUniquePtr(T* InPtr) : Ptr(InPtr) {}

	TUniquePtr(TUniquePtr&& Other) noexcept : Ptr(Other.Ptr) { Other.Ptr = nullptr; }

	TUniquePtr& operator=(TUniquePtr&& Other) noexcept
	{
		if (this != &Other)
		{
			delete Ptr;
			Ptr = Other.Ptr;
			Other.Ptr = nullptr;
		}
		return *this;
	}

	TUniquePtr(const TUniquePtr&) = delete;
	TUniquePtr& operator=(const TUniquePtr&) = delete;

	~TUniquePtr() { delete Ptr; }

	T* operator->() const { return Ptr; }
	T& operator*() const { return *Ptr; }
	T* Get() const { return Ptr; }
	bool IsValid() const { return Ptr != nullptr; }

private:
	T* Ptr = nullptr;
};

template <typename T, typename... TArgs>
TUniquePtr<T> MakeUnique(TArgs&&... Args)
{
	return TUniquePtr<T>(new T(std::forward<TArgs>(Args)...));
}

// -- object model -----------------------------------------------------------

class UObject
{
public:
	virtual ~UObject() = default;
};

class AActor : public UObject
{
public:
	FVector GetActorLocation() const { return Location; }
	void SetActorLocation(const FVector& NewLocation) { Location = NewLocation; }

private:
	FVector Location;
};

template <typename T>
bool IsValid(const T* Object)
{
	return Object != nullptr;
}
