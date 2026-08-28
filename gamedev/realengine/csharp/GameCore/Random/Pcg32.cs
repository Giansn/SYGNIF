using System;
using System.Collections.Generic;

namespace GameCore.Random
{
    /// <summary>
    /// PCG32 — a small, fast, deterministic pseudo-random generator.
    /// </summary>
    /// <remarks>
    /// <para>
    /// Neither engine gives you a usable deterministic RNG out of the box.
    /// Unity's <c>UnityEngine.Random</c> is a single global with hidden state that
    /// the engine itself draws from (particle systems and some built-in effects
    /// advance it), so a seeded run is not reproducible in practice. Unreal's
    /// <c>FRandomStream</c> is genuinely seedable and much closer to correct, but
    /// it is a plain LCG whose low bits are weak and whose stream is tied to the
    /// engine version.
    /// </para>
    /// <para>
    /// So a game that needs reproducible worlds, replayable runs or a
    /// "share this seed" feature owns its generator. This is the same PCG32 as
    /// <c>gamedev/engine/rng.py</c> and produces the identical stream, verified
    /// against the reference implementation's published test vectors.
    /// </para>
    /// <para>
    /// Everything is <see cref="ulong"/> arithmetic, which wraps silently in C#
    /// by default — exactly the behaviour PCG needs.
    /// </para>
    /// </remarks>
    public sealed class Pcg32
    {
        private const ulong Multiplier = 6364136223846793005UL;
        private const ulong DefaultStream = 1442695040888963407UL;

        private ulong _state;
        private ulong _increment;

        /// <summary>Creates a generator.</summary>
        /// <param name="seed">Selects the position within a sequence.</param>
        /// <param name="stream">
        /// Selects one of 2^63 distinct sequences. Two generators with the same
        /// seed but different streams are independent, which is how each subsystem
        /// gets its own randomness derived from one world seed. Sharing a single
        /// stream between, say, terrain and loot means adding one terrain draw
        /// shifts every subsequent loot roll and the whole world changes.
        /// </param>
        public Pcg32(ulong seed = 0UL, ulong stream = DefaultStream)
        {
            _increment = (stream << 1) | 1UL;
            _state = 0UL;
            NextUInt32();
            _state += seed;
            NextUInt32();
        }

        /// <summary>A uniformly distributed 32-bit value.</summary>
        public uint NextUInt32()
        {
            var old = _state;
            _state = unchecked(old * Multiplier + _increment);

            // An LCG's low bits are famously poor, so PCG never hands the state
            // out directly: fold the high bits down, then rotate by an amount
            // also taken from the state. That output permutation is what makes a
            // mediocre generator good without a large state.
            var xorshifted = (uint)(((old >> 18) ^ old) >> 27);
            var rot = (int)(old >> 59);
            return (xorshifted >> rot) | (xorshifted << ((-rot) & 31));
        }

        /// <summary>A double in <c>[0, 1)</c>.</summary>
        /// <remarks>
        /// Built from 53 bits, the exact width of a double's mantissa, so every
        /// representable value in the range is reachable and equally likely.
        /// Dividing a single 32-bit draw by 2^32 yields only four billion distinct
        /// values with visible gaps between them.
        /// </remarks>
        public double NextDouble()
        {
            ulong high = NextUInt32() >> 5;  // 27 bits
            ulong low = NextUInt32() >> 6;   // 26 bits
            return ((high << 26) | low) * (1.0 / 9007199254740992.0); // 2^53
        }

        /// <summary>A float in <c>[0, 1)</c>, the form gameplay code usually wants.</summary>
        public float NextFloat() => (float)NextDouble();

        /// <summary>A uniform integer in <c>[0, bound)</c>, with no modulo bias.</summary>
        /// <remarks>
        /// The naive <c>NextUInt32() % bound</c> is subtly wrong: unless the bound
        /// divides 2^32 exactly, low residues get one extra chance each. For a d6
        /// the skew is invisible; near 2^31 it approaches 2:1. Rejection sampling
        /// discards draws landing in the short final block and rolls again.
        /// </remarks>
        public int Below(int bound)
        {
            if (bound <= 0)
            {
                throw new ArgumentOutOfRangeException(nameof(bound), "must be positive");
            }

            var limit = (uint)bound;
            var threshold = (uint)((0x100000000UL - limit) % limit); // 2^32 mod bound
            while (true)
            {
                var candidate = NextUInt32();
                if (candidate >= threshold)
                {
                    return (int)(candidate % limit);
                }
            }
        }

        /// <summary>A uniform integer in <c>[min, max]</c>, both ends inclusive.</summary>
        public int Range(int min, int max)
        {
            if (max < min)
            {
                throw new ArgumentOutOfRangeException(nameof(max), "must be >= min");
            }

            return min + Below(max - min + 1);
        }

        /// <summary>A float in <c>[min, max)</c>.</summary>
        public float Range(float min, float max) => min + ((max - min) * NextFloat());

        /// <summary>True with the given probability.</summary>
        public bool Chance(double probability) => NextDouble() < probability;

        /// <summary>Picks an element uniformly.</summary>
        public T Pick<T>(IReadOnlyList<T> items)
        {
            if (items == null)
            {
                throw new ArgumentNullException(nameof(items));
            }

            if (items.Count == 0)
            {
                throw new ArgumentException("cannot pick from an empty list", nameof(items));
            }

            return items[Below(items.Count)];
        }

        /// <summary>Fisher-Yates shuffle, in place.</summary>
        /// <remarks>
        /// Iterating downwards and swapping with an index in <c>[0, i]</c> is the
        /// correct form. The common bug — picking from the whole list each step —
        /// produces n^n equally likely swap sequences over n! orderings, and is
        /// therefore measurably biased.
        /// </remarks>
        public void Shuffle<T>(IList<T> items)
        {
            if (items == null)
            {
                throw new ArgumentNullException(nameof(items));
            }

            for (var i = items.Count - 1; i > 0; i--)
            {
                var j = Below(i + 1);
                (items[i], items[j]) = (items[j], items[i]);
            }
        }

        /// <summary>Snapshots the state, for save games and replay files.</summary>
        public (ulong State, ulong Increment) Snapshot() => (_state, _increment);

        /// <summary>Restores a snapshot.</summary>
        public void Restore((ulong State, ulong Increment) snapshot)
        {
            _state = snapshot.State;
            _increment = snapshot.Increment;
        }

        /// <summary>
        /// Turns a human-typed seed such as "cavern" into a 64-bit value using
        /// FNV-1a, which is stable across processes — unlike <see cref="string.GetHashCode()"/>,
        /// which .NET randomises per process precisely so it cannot be relied on.
        /// </summary>
        public static ulong SeedFromText(string text)
        {
            if (text == null)
            {
                throw new ArgumentNullException(nameof(text));
            }

            var value = 0xCBF29CE484222325UL;
            foreach (var b in System.Text.Encoding.UTF8.GetBytes(text))
            {
                value = unchecked((value ^ b) * 0x100000001B3UL);
            }

            return value;
        }
    }
}
