#pragma once

// Pcg32 — deterministic pseudo-random numbers for the gameplay core.
//
// Unreal's FRandomStream is genuinely seedable, which already puts it ahead of
// UnityEngine.Random, and for most gameplay it is the right choice. Two reasons
// to own the generator anyway:
//
//   * FRandomStream is a plain LCG. Its low bits are weak, so the common
//     GetUnsignedInt() % N pattern is biased twice over — once by the LCG's
//     structure and once by the modulo. That is invisible for a loot roll and
//     very much not invisible for procedural generation that draws millions of
//     numbers.
//   * Its exact stream is an implementation detail of the engine version. A
//     seed that generated a particular world in UE 5.3 is not guaranteed to
//     generate it in 5.5, which quietly breaks saved seeds and replay files.
//
// This is the same PCG32 as the Python and C# implementations in this
// repository and produces a byte-identical stream, which is what lets a world
// seed mean the same thing in tools, tests and the shipped game.
//
// No exceptions: Unreal game modules compile with bEnableExceptions = false, so
// invalid input is handled by clamping and by an assert in debug builds rather
// than by throwing.

#include <cassert>
#include <cstdint>
#include <cstddef>

namespace gamecore
{

class Pcg32
{
public:
    // `Stream` selects one of 2^63 independent sequences. Two generators with
    // the same seed but different streams do not interfere, which is how each
    // subsystem gets its own randomness from a single world seed. Sharing one
    // stream between terrain and loot means adding a single terrain draw shifts
    // every subsequent loot roll and the whole world changes.
    explicit Pcg32(uint64_t Seed = 0, uint64_t Stream = 1442695040888963407ULL)
    {
        Increment = (Stream << 1u) | 1u;
        State = 0u;
        NextUInt32();
        State += Seed;
        NextUInt32();
    }

    uint32_t NextUInt32()
    {
        const uint64_t Old = State;
        State = Old * 6364136223846793005ULL + Increment;

        // An LCG's low bits are poor, so PCG never hands the state out directly:
        // fold the high bits down, then rotate by an amount also taken from the
        // state. That output permutation is what makes a small, fast generator
        // good without a large state.
        const uint32_t XorShifted = static_cast<uint32_t>(((Old >> 18u) ^ Old) >> 27u);
        const uint32_t Rot = static_cast<uint32_t>(Old >> 59u);
        return (XorShifted >> Rot) | (XorShifted << ((0u - Rot) & 31u));
    }

    // A double in [0, 1), built from 53 bits — the exact width of a double's
    // mantissa — so every representable value in the range is reachable and
    // equally likely. Dividing one 32-bit draw by 2^32 yields only four billion
    // distinct values with visible gaps between them.
    double NextDouble()
    {
        const uint64_t High = NextUInt32() >> 5;  // 27 bits
        const uint64_t Low = NextUInt32() >> 6;   // 26 bits
        return static_cast<double>((High << 26) | Low) * (1.0 / 9007199254740992.0);
    }

    float NextFloat() { return static_cast<float>(NextDouble()); }

    // A uniform integer in [0, Bound), with no modulo bias.
    //
    // The naive NextUInt32() % Bound is subtly wrong: unless Bound divides 2^32
    // exactly, low residues get one extra chance each. For a d6 the skew is
    // invisible; near 2^31 it approaches 2:1. Rejection sampling throws away
    // draws landing in the short final block and rolls again.
    uint32_t Below(uint32_t Bound)
    {
        assert(Bound > 0 && "bound must be positive");
        if (Bound == 0)
        {
            return 0;
        }

        const uint32_t Threshold = static_cast<uint32_t>((0x100000000ULL - Bound) % Bound);
        for (;;)
        {
            const uint32_t Candidate = NextUInt32();
            if (Candidate >= Threshold)
            {
                return Candidate % Bound;
            }
        }
    }

    // A uniform integer in [Min, Max], both ends inclusive.
    int32_t Range(int32_t Min, int32_t Max)
    {
        assert(Max >= Min && "max must be >= min");
        if (Max <= Min)
        {
            return Min;
        }
        return Min + static_cast<int32_t>(Below(static_cast<uint32_t>(Max - Min + 1)));
    }

    float RangeFloat(float Min, float Max) { return Min + (Max - Min) * NextFloat(); }

    bool Chance(double Probability) { return NextDouble() < Probability; }

    // Fisher-Yates, in place. Iterating downwards and swapping with an index in
    // [0, i] is the correct form; the common bug of picking from the whole array
    // each step produces n^n equally likely swap sequences over n! orderings and
    // is measurably biased.
    template <typename T>
    void Shuffle(T* Items, size_t Count)
    {
        if (Items == nullptr || Count < 2)
        {
            return;
        }

        for (size_t i = Count - 1; i > 0; --i)
        {
            const uint32_t j = Below(static_cast<uint32_t>(i + 1));
            T Temp = Items[i];
            Items[i] = Items[j];
            Items[j] = Temp;
        }
    }

    // For save games and replay files.
    void Snapshot(uint64_t& OutState, uint64_t& OutIncrement) const
    {
        OutState = State;
        OutIncrement = Increment;
    }

    void Restore(uint64_t InState, uint64_t InIncrement)
    {
        State = InState;
        Increment = InIncrement;
    }

    // Turns a human-typed seed such as "cavern" into a 64-bit value, using FNV-1a
    // so it is stable across processes and platforms.
    static uint64_t SeedFromText(const char* Text)
    {
        uint64_t Value = 0xCBF29CE484222325ULL;
        if (Text == nullptr)
        {
            return Value;
        }

        for (const char* P = Text; *P != '\0'; ++P)
        {
            Value = (Value ^ static_cast<uint8_t>(*P)) * 0x100000001B3ULL;
        }
        return Value;
    }

private:
    uint64_t State = 0;
    uint64_t Increment = 0;
};

} // namespace gamecore
