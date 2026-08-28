#pragma once

// Vec2 — a 2D vector for the engine-free gameplay core.
//
// Unreal already has FVector2D, and inside an Unreal module you would normally
// use it. This type exists because the core must compile and be tested with no
// Unreal headers at all: FVector2D drags in Core, which drags in the build
// system, which means the only way to run a test is to build the editor. A
// two-float struct is a cheap price for tests that run in milliseconds.
//
// Conversion happens once, at the adapter boundary:
//     FVector2D ToUnreal(const gamecore::Vec2& v) { return FVector2D(v.X, v.Y); }
//
// Note the axis convention difference this papers over. Unreal is Z-up with X
// forward, so a 2D gameplay plane is usually (X, Y) viewed from above, whereas
// a side-on 2D game maps to (X, Z). The core stays agnostic: it calls them X
// and Y and the adapter decides which world axes those are.

#include <cmath>

namespace gamecore
{

// Exact float comparison, deliberately, in the two places it is actually the
// right question.
//
// -Wfloat-equal is a good warning and is on for this build, because comparing
// computed floats with == is nearly always a mistake. It does not apply to
// "is this bit-pattern exactly zero", which is what a divide-by-zero guard
// asks: an epsilon there would either still divide by a denormal or reject
// motion that is merely very slow. Funnelling every such comparison through
// these two helpers keeps the suppression to one place instead of scattering
// pragmas through the collision code.
#if defined(__GNUC__) || defined(__clang__)
#pragma GCC diagnostic push
#pragma GCC diagnostic ignored "-Wfloat-equal"
#endif

constexpr bool IsExactlyZero(float Value) { return Value == 0.0f; }
constexpr bool ExactlyEquals(float A, float B) { return A == B; }

#if defined(__GNUC__) || defined(__clang__)
#pragma GCC diagnostic pop
#endif

struct Vec2
{
    float X = 0.0f;
    float Y = 0.0f;

    constexpr Vec2() = default;
    constexpr Vec2(float InX, float InY) : X(InX), Y(InY) {}

    constexpr Vec2 operator+(const Vec2& Other) const { return Vec2(X + Other.X, Y + Other.Y); }
    constexpr Vec2 operator-(const Vec2& Other) const { return Vec2(X - Other.X, Y - Other.Y); }
    constexpr Vec2 operator*(float Scalar) const { return Vec2(X * Scalar, Y * Scalar); }
    constexpr Vec2 operator/(float Scalar) const { return Vec2(X / Scalar, Y / Scalar); }
    constexpr Vec2 operator-() const { return Vec2(-X, -Y); }

    Vec2& operator+=(const Vec2& Other) { X += Other.X; Y += Other.Y; return *this; }
    Vec2& operator-=(const Vec2& Other) { X -= Other.X; Y -= Other.Y; return *this; }
    Vec2& operator*=(float Scalar) { X *= Scalar; Y *= Scalar; return *this; }

    constexpr bool operator==(const Vec2& Other) const
    {
        return ExactlyEquals(X, Other.X) && ExactlyEquals(Y, Other.Y);
    }

    constexpr bool operator!=(const Vec2& Other) const { return !(*this == Other); }

    constexpr float Dot(const Vec2& Other) const { return X * Other.X + Y * Other.Y; }

    // The z component of the 3D cross product. Its sign says which side of this
    // vector another one lies on, which is how you answer "is this turn left or
    // right" without any trigonometry.
    constexpr float Cross(const Vec2& Other) const { return X * Other.Y - Y * Other.X; }

    float Length() const { return std::sqrt(X * X + Y * Y); }

    // Comparing distances is the most common vector operation in a game
    // (nearest enemy, is-in-range, culling). sqrt is monotonic, so comparing
    // squared lengths gives the same answer and skips the expensive call.
    constexpr float LengthSquared() const { return X * X + Y * Y; }

    // Returns the zero vector rather than dividing by zero. Normalising a
    // stationary entity's velocity happens constantly, and an assert there
    // would force a guard clause at every call site.
    Vec2 Normalized() const
    {
        const float Len = Length();
        return Len > 1e-8f ? Vec2(X / Len, Y / Len) : Vec2(0.0f, 0.0f);
    }

    // Same direction, magnitude capped — terminal velocity.
    Vec2 Clamped(float MaxLength) const
    {
        const float LenSq = LengthSquared();
        if (LenSq <= MaxLength * MaxLength || LenSq < 1e-16f)
        {
            return *this;
        }
        return Normalized() * MaxLength;
    }

    // Mirror about a surface with the given unit normal: v - 2(v.n)n. This is
    // the whole of "ball bounces off wall" — the component along the normal
    // flips, the component along the surface is untouched.
    constexpr Vec2 Reflected(const Vec2& Normal) const
    {
        return *this - Normal * (2.0f * Dot(Normal));
    }

    static constexpr Vec2 Zero() { return Vec2(0.0f, 0.0f); }
};

constexpr Vec2 operator*(float Scalar, const Vec2& V) { return V * Scalar; }

inline float Distance(const Vec2& A, const Vec2& B) { return (A - B).Length(); }
constexpr float DistanceSquared(const Vec2& A, const Vec2& B) { return (A - B).LengthSquared(); }

constexpr float Clamp(float Value, float Low, float High)
{
    return Value < Low ? Low : (Value > High ? High : Value);
}

constexpr float Lerp(float A, float B, float T) { return A + (B - A) * T; }

constexpr Vec2 Min(const Vec2& A, const Vec2& B)
{
    return Vec2(A.X < B.X ? A.X : B.X, A.Y < B.Y ? A.Y : B.Y);
}

constexpr Vec2 Max(const Vec2& A, const Vec2& B)
{
    return Vec2(A.X > B.X ? A.X : B.X, A.Y > B.Y ? A.Y : B.Y);
}

} // namespace gamecore
