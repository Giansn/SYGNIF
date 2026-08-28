using System;
using System.Collections.Generic;
using GameCore.Random;
using Xunit;

namespace GameCore.Tests
{
    public class Pcg32Tests
    {
        /// <summary>
        /// The canonical PCG32 demo output for seed=42, stream=54. Matching it proves
        /// this really is PCG32 rather than merely "some generator that looks random".
        /// </summary>
        [Fact]
        public void MatchesReferenceImplementation()
        {
            var expected = new uint[] { 0xA15C02B7, 0x7B47F409, 0xBA1D3330, 0x83D2F293, 0xBFA4784B, 0xCBED606E };
            var rng = new Pcg32(seed: 42, stream: 54);

            for (var i = 0; i < expected.Length; i++)
            {
                Assert.Equal(expected[i], rng.NextUInt32());
            }
        }

        /// <summary>
        /// Cross-language parity with the Python implementation in
        /// <c>gamedev/engine/rng.py</c>. This is the test that makes the port real:
        /// the same world seed has to produce the same world in either language, or
        /// the Python version stops being a usable reference for the C# one.
        /// </summary>
        [Fact]
        public void ProducesTheSameStreamAsThePythonImplementation()
        {
            var expected = new uint[]
            {
                0x87EDE634, 0x3433FC18, 0x810D4AE3, 0xCD51E088,
                0x48954F0E, 0x4BB5BD5A, 0x12F4D398, 0x64EE48C6,
            };
            var rng = new Pcg32(seed: 12345, stream: 1);

            foreach (var value in expected)
            {
                Assert.Equal(value, rng.NextUInt32());
            }
        }

        [Fact]
        public void ProducesTheSameDoublesAsPython()
        {
            var expected = new[]
            {
                0.53097380104600056, 0.50410908313118363,
                0.2835282705406984, 0.074048253851092816,
            };
            var rng = new Pcg32(seed: 12345, stream: 1);

            foreach (var value in expected)
            {
                Assert.Equal(value, rng.NextDouble(), 15);
            }
        }

        [Fact]
        public void ProducesTheSameBoundedIntegersAsPython()
        {
            var expected = new[] { 4, 1, 5, 1, 1, 5, 4, 3, 2, 1, 4, 5 };
            var rng = new Pcg32(seed: 777, stream: 42);

            foreach (var value in expected)
            {
                Assert.Equal(value, rng.Below(6));
            }
        }

        [Fact]
        public void ShufflesIdenticallyToPython()
        {
            var deck = new List<int> { 0, 1, 2, 3, 4, 5, 6, 7, 8, 9 };
            new Pcg32(seed: 2026, stream: 7).Shuffle(deck);
            Assert.Equal(new[] { 1, 3, 8, 7, 0, 4, 9, 6, 5, 2 }, deck);
        }

        [Fact]
        public void SeedFromTextMatchesPythonAndIsProcessStable()
        {
            Assert.Equal(0x6712D974B5E1C470UL, Pcg32.SeedFromText("cavern"));
            Assert.Equal(0xCBF29CE484222325UL, Pcg32.SeedFromText(string.Empty));
            Assert.NotEqual(Pcg32.SeedFromText("cavern"), Pcg32.SeedFromText("caverm"));
        }

        [Fact]
        public void SameSeedGivesSameSequence()
        {
            var a = new Pcg32(1234);
            var b = new Pcg32(1234);

            for (var i = 0; i < 64; i++)
            {
                Assert.Equal(a.NextDouble(), b.NextDouble());
            }
        }

        [Fact]
        public void StreamsAreIndependent()
        {
            // Same seed, different stream: distinct sequences. This is what lets
            // terrain and loot share a world seed without interfering.
            var terrain = new Pcg32(seed: 7, stream: 1);
            var loot = new Pcg32(seed: 7, stream: 2);
            var differences = 0;

            for (var i = 0; i < 32; i++)
            {
                if (terrain.NextUInt32() != loot.NextUInt32())
                {
                    differences++;
                }
            }

            Assert.Equal(32, differences);
        }

        [Fact]
        public void NextDoubleStaysInUnitInterval()
        {
            var rng = new Pcg32(99);
            for (var i = 0; i < 20_000; i++)
            {
                var value = rng.NextDouble();
                Assert.InRange(value, 0.0, 0.9999999999999999);
            }
        }

        [Fact]
        public void BelowStaysInRange()
        {
            var rng = new Pcg32(5);
            foreach (var bound in new[] { 1, 2, 6, 52, 1000 })
            {
                for (var i = 0; i < 2_000; i++)
                {
                    Assert.InRange(rng.Below(bound), 0, bound - 1);
                }
            }
        }

        [Fact]
        public void BelowIsCloseToUniform()
        {
            // Rejection sampling should leave no visible skew across buckets.
            var rng = new Pcg32(17);
            var buckets = new int[6];
            const int rolls = 60_000;

            for (var i = 0; i < rolls; i++)
            {
                buckets[rng.Below(6)]++;
            }

            const double expected = rolls / 6.0;
            foreach (var count in buckets)
            {
                Assert.True(Math.Abs(count - expected) / expected < 0.05, $"skewed: [{string.Join(", ", buckets)}]");
            }
        }

        [Fact]
        public void BelowRejectsNonPositiveBounds()
        {
            var rng = new Pcg32(1);
            Assert.Throws<ArgumentOutOfRangeException>(() => rng.Below(0));
            Assert.Throws<ArgumentOutOfRangeException>(() => rng.Below(-3));
        }

        [Fact]
        public void RangeIsInclusiveAtBothEnds()
        {
            var rng = new Pcg32(3);
            var seen = new HashSet<int>();
            for (var i = 0; i < 500; i++)
            {
                seen.Add(rng.Range(1, 3));
            }

            Assert.Equal(new HashSet<int> { 1, 2, 3 }, seen);
        }

        [Fact]
        public void ShuffleIsAPermutation()
        {
            var deck = new List<int>();
            for (var i = 0; i < 50; i++)
            {
                deck.Add(i);
            }

            var original = new List<int>(deck);
            new Pcg32(8).Shuffle(deck);

            Assert.NotEqual(original, deck);
            deck.Sort();
            Assert.Equal(original, deck);
        }

        [Fact]
        public void ShuffleHasNoPositionalBias()
        {
            // A biased shuffle (picking from the whole list each step) shows up as
            // some element favouring some slot.
            var rng = new Pcg32(21);
            var landings = new int[4];
            const int trials = 20_000;

            for (var i = 0; i < trials; i++)
            {
                var deck = new List<int> { 0, 1, 2, 3 };
                rng.Shuffle(deck);
                landings[deck.IndexOf(0)]++;
            }

            const double expected = trials / 4.0;
            foreach (var count in landings)
            {
                Assert.True(Math.Abs(count - expected) / expected < 0.06, $"biased: [{string.Join(", ", landings)}]");
            }
        }

        [Fact]
        public void SnapshotReplaysExactly()
        {
            var rng = new Pcg32(404);
            for (var i = 0; i < 10; i++)
            {
                rng.NextDouble();
            }

            var snapshot = rng.Snapshot();
            var expected = new double[10];
            for (var i = 0; i < 10; i++)
            {
                expected[i] = rng.NextDouble();
            }

            rng.Restore(snapshot);
            for (var i = 0; i < 10; i++)
            {
                Assert.Equal(expected[i], rng.NextDouble());
            }
        }
    }
}
