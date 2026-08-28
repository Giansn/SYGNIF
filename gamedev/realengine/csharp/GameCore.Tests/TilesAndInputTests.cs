using System;
using System.Collections.Generic;
using System.Numerics;
using GameCore.Geometry;
using GameCore.Input;
using GameCore.Tiles;
using Xunit;

namespace GameCore.Tests
{
    public class TileGridTests
    {
        // Authored the way it looks on screen: the roof is the first line.
        private static readonly string[] Level =
        {
            "#####",
            "#...#",
            "#.=.#",
            "#...#",
            "#####",
        };

        [Fact]
        public void AsciiIsFlippedSoRowZeroIsTheBottom()
        {
            // The single most error-prone part of the port. The first authored line
            // is the top of the screen, but world row 0 must be the ground, or every
            // level loads upside down and every gravity sign is wrong.
            var grid = TileGrid.FromAscii(Level);

            Assert.Equal(Tile.Solid, grid.Get(0, 0)); // bottom-left corner
            Assert.Equal(Tile.Solid, grid.Get(2, 0)); // bottom wall
            Assert.Equal(Tile.Solid, grid.Get(2, 4)); // top wall
            Assert.Equal(Tile.OneWay, grid.Get(2, 2)); // the platform, dead centre
            Assert.Equal(Tile.Empty, grid.Get(1, 1));
        }

        [Fact]
        public void OutOfBoundsIsSolidExceptAbove()
        {
            // Solid to the sides and below means an entity can never leave the level
            // through a wall with no edge special-casing; open sky means a jump near
            // the ceiling is not mysteriously blocked by nothing.
            var grid = TileGrid.FromAscii(Level);

            Assert.Equal(Tile.Solid, grid.Get(-1, 2));
            Assert.Equal(Tile.Solid, grid.Get(99, 2));
            Assert.Equal(Tile.Solid, grid.Get(2, -1));
            Assert.Equal(Tile.Empty, grid.Get(2, 99));
        }

        [Fact]
        public void WorldConversionRoundTrips()
        {
            var grid = new TileGrid(10, 10, tileSize: 16f);
            Assert.Equal((2, 3), grid.WorldToTile(new Vector2(40, 55)));
            Assert.Equal(new Vector2(40, 56), grid.TileToWorld(2, 3));
            Assert.Equal(new Vector2(32, 48), grid.BoxFor(2, 3).Min);
        }

        [Fact]
        public void WorldToTileHandlesNegativeCoordinates()
        {
            // Truncation towards zero would map -0.5 and +0.5 to the same tile,
            // which puts an entity standing just left of the origin inside the wrong
            // cell. Flooring is required.
            var grid = new TileGrid(10, 10, tileSize: 16f);
            Assert.Equal((-1, -1), grid.WorldToTile(new Vector2(-1, -1)));
            Assert.Equal((-1, -1), grid.WorldToTile(new Vector2(-16, -16)));
            Assert.Equal((-2, -2), grid.WorldToTile(new Vector2(-17, -17)));
        }

        [Fact]
        public void FlushEdgeDoesNotIncludeTheNextTile()
        {
            // A box whose right edge sits exactly on a boundary does not overlap the
            // tile beyond it. Including that column makes an entity collide with a
            // wall it is merely standing against.
            var grid = new TileGrid(10, 10, tileSize: 16f);
            var box = Aabb.FromSize(new Vector2(0, 0), new Vector2(16, 16));
            var (minX, minY, maxX, maxY) = grid.TileRange(box);

            Assert.Equal((0, 0, 0, 0), (minX, minY, maxX, maxY));
        }

        [Fact]
        public void BroadphaseOnlyLooksAtNearbyTiles()
        {
            // The rule that makes tile maps viable: cost scales with the size of the
            // entity, not the size of the level.
            var big = new TileGrid(500, 500, tileSize: 16f);
            for (var x = 0; x < 500; x++)
            {
                big.Set(x, 0, Tile.Solid);
            }

            var box = Aabb.FromSize(new Vector2(100, 0), new Vector2(16, 16));
            var (minX, minY, maxX, maxY) = big.TileRange(box);
            var inspected = (maxX - minX + 1) * (maxY - minY + 1);

            Assert.True(inspected <= 4, $"inspected {inspected} tiles for a one-tile entity");
            Assert.Equal(2, big.BoxesOverlapping(box).Count);
        }

        [Fact]
        public void BoxesOverlappingReturnsOnlyRequestedIds()
        {
            var grid = TileGrid.FromAscii(Level, tileSize: 1f);
            var whole = Aabb.FromSize(Vector2.Zero, new Vector2(5, 5));

            Assert.Equal(16, grid.BoxesOverlapping(whole).Count); // the solid ring
            Assert.Single(grid.BoxesOverlapping(whole, new HashSet<byte> { Tile.OneWay }));
        }

        [Fact]
        public void OverlapsAnyDetectsHazards()
        {
            var grid = TileGrid.FromAscii(new[] { "...", ".^.", "..." }, tileSize: 1f);
            var hazards = new HashSet<byte> { Tile.Hazard };

            Assert.True(grid.OverlapsAny(Aabb.FromSize(new Vector2(1, 1), new Vector2(1, 1)), hazards));
            Assert.False(grid.OverlapsAny(Aabb.FromSize(new Vector2(0, 0), new Vector2(1, 1)), hazards));
        }

        [Fact]
        public void FindLocatesTiles()
        {
            var grid = TileGrid.FromAscii(new[] { "..G", "...", "#.." }, tileSize: 1f);
            Assert.Equal(new List<(int, int)> { (2, 2) }, grid.Find(Tile.Goal));
            Assert.Equal(new List<(int, int)> { (0, 0) }, grid.Find(Tile.Solid));
        }

        [Fact]
        public void RejectsUnknownLegendCharacters()
        {
            Assert.Throws<ArgumentException>(() => TileGrid.FromAscii(new[] { "#?#" }));
        }

        [Fact]
        public void RejectsEmptyLevels()
        {
            Assert.Throws<ArgumentException>(() => TileGrid.FromAscii(Array.Empty<string>()));
        }
    }

    public class ActionStateTests
    {
        [Fact]
        public void JustPressedFiresExactlyOncePerTick()
        {
            // The Unity FixedUpdate trap: if edges are rolled per rendered frame
            // rather than per tick, one tap on jump becomes three jumps whenever a
            // frame happens to run three ticks.
            var input = new ActionState();
            input.Press("Jump");

            input.BeginTick();
            Assert.True(input.JustPressed("Jump"));
            Assert.True(input.IsHeld("Jump"));

            input.BeginTick();
            Assert.False(input.JustPressed("Jump"));
            Assert.True(input.IsHeld("Jump"));

            input.BeginTick();
            Assert.False(input.JustPressed("Jump"));
        }

        [Fact]
        public void ATapShorterThanOneTickIsNotLost()
        {
            // At 60 Hz a tick is 16 ms; a fast player or a 1000 Hz mouse can press
            // and release inside it. Snapshot comparison alone shows the button down
            // at neither end and the input silently vanishes.
            var input = new ActionState();
            input.BeginTick();

            input.Press("Fire");
            input.Release("Fire");

            input.BeginTick();
            Assert.True(input.JustPressed("Fire"));
            Assert.False(input.IsHeld("Fire"));
            Assert.True(input.JustReleased("Fire"));
        }

        [Fact]
        public void ReleaseIsDetected()
        {
            var input = new ActionState();
            input.Press("Jump");
            input.BeginTick();

            input.Release("Jump");
            input.BeginTick();

            Assert.True(input.JustReleased("Jump"));
            Assert.False(input.IsHeld("Jump"));

            input.BeginTick();
            Assert.False(input.JustReleased("Jump"));
        }

        [Fact]
        public void AxisReturnsZeroWhenBothDirectionsAreHeld()
        {
            // Picking a winner produces the notorious bug where tapping left while
            // running right makes the character sprint the wrong way.
            var input = new ActionState();
            input.Press("Left");
            input.Press("Right");
            input.BeginTick();

            Assert.Equal(0f, input.Axis("Left", "Right"));

            input.Release("Left");
            input.BeginTick();
            Assert.Equal(1f, input.Axis("Left", "Right"));
        }

        [Fact]
        public void ClearDropsStuckKeysOnFocusLoss()
        {
            // Without this, alt-tabbing mid-stride leaves the movement key stuck
            // down forever and the character walks into a wall on return.
            var input = new ActionState();
            input.Press("Right");
            input.BeginTick();
            Assert.True(input.IsHeld("Right"));

            input.Clear();
            input.BeginTick();
            Assert.False(input.IsHeld("Right"));
            Assert.False(input.JustPressed("Right"));
        }

        [Fact]
        public void UnknownActionsAreSimplyNotHeld()
        {
            var input = new ActionState();
            input.BeginTick();
            Assert.False(input.IsHeld("NeverBound"));
            Assert.False(input.JustPressed("NeverBound"));
            Assert.Equal(0f, input.Axis("NopeA", "NopeB"));
        }

        [Fact]
        public void NullActionsAreRejected()
        {
            var input = new ActionState();
            Assert.Throws<ArgumentNullException>(() => input.Press(null!));
            Assert.Throws<ArgumentNullException>(() => input.Release(null!));
        }
    }
}
