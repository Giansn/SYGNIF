using System;
using System.Collections.Generic;
using System.Numerics;
using GameCore.Geometry;

namespace GameCore.Tiles
{
    /// <summary>Tile identifiers understood by <see cref="TileGrid"/>.</summary>
    public static class Tile
    {
        /// <summary>Passable space.</summary>
        public const byte Empty = 0;

        /// <summary>Blocks movement from every direction.</summary>
        public const byte Solid = 1;

        /// <summary>Blocks only from above, so an entity can jump up through it.</summary>
        public const byte OneWay = 2;

        /// <summary>Damages whatever enters it.</summary>
        public const byte Hazard = 3;

        /// <summary>Level exit.</summary>
        public const byte Goal = 4;
    }

    /// <summary>
    /// A rectangular grid of tile ids, with the broadphase that makes it useful.
    /// </summary>
    /// <remarks>
    /// <para>
    /// Both engines have their own tilemap: Unity's <c>Tilemap</c> plus
    /// <c>TilemapCollider2D</c>, and Unreal's <c>Paper2D</c> tile maps. Those own
    /// rendering and physics colliders, which is work worth delegating. What they
    /// do not own is your gameplay's view of the level — line of sight, spawn
    /// rules, pathfinding, "is this tile safe to stand on" — and routing every one
    /// of those questions through the engine's collider system is both slow and
    /// untestable outside play mode. Keeping an authoritative grid in the core and
    /// treating the engine's tilemap as a rendering mirror is the pattern.
    /// </para>
    /// <para>
    /// <b>The rule that matters:</b> never iterate the whole map. A 200x60 level is
    /// 12 000 tiles; testing an entity against all of them, sixty times a second,
    /// per entity, is the difference between a game and a slideshow. Because it is
    /// a grid, the tiles an entity could possibly touch are found by dividing its
    /// bounds by the tile size, so <see cref="BoxesOverlapping"/> costs what the
    /// entity is big, not what the level is big.
    /// </para>
    /// <para>
    /// <b>Coordinate convention:</b> row 0 is the <em>bottom</em> of the world, matching
    /// Unity's Y-up 2D space, so tile (x, y) sits at world
    /// <c>(x * TileSize, y * TileSize)</c> with no flip anywhere in the maths.
    /// ASCII levels are authored the way they look — first line is the top of the
    /// screen — and <see cref="FromAscii"/> reverses them on load. Doing the flip
    /// once, at the authoring boundary, is what keeps sign errors out of the
    /// gameplay code. The Python original used Y-down throughout because it drew
    /// straight into a framebuffer; that is exactly the assumption that does not
    /// survive a port.
    /// </para>
    /// </remarks>
    public sealed class TileGrid
    {
        private readonly byte[] _tiles;

        /// <summary>Creates an empty grid.</summary>
        /// <param name="width">Columns.</param>
        /// <param name="height">Rows.</param>
        /// <param name="tileSize">World units per tile.</param>
        public TileGrid(int width, int height, float tileSize = 1f)
        {
            if (width <= 0 || height <= 0)
            {
                throw new ArgumentOutOfRangeException(nameof(width), "grid must have positive dimensions");
            }

            if (tileSize <= 0f)
            {
                throw new ArgumentOutOfRangeException(nameof(tileSize), "must be positive");
            }

            Width = width;
            Height = height;
            TileSize = tileSize;
            _tiles = new byte[width * height];
            SolidIds = new HashSet<byte> { Tile.Solid };
        }

        /// <summary>Columns.</summary>
        public int Width { get; }

        /// <summary>Rows.</summary>
        public int Height { get; }

        /// <summary>World units per tile.</summary>
        public float TileSize { get; }

        /// <summary>Which tile ids block movement.</summary>
        public HashSet<byte> SolidIds { get; }

        /// <summary>
        /// Builds a grid from ASCII art, where the first string is the top row.
        /// </summary>
        /// <remarks>
        /// Levels as text are readable in a diff, editable without a tool and
        /// reviewable in a pull request. Every real project outgrows this and builds
        /// an editor — in Unity you would use the Tile Palette — but starting here
        /// means the first level exists in minutes rather than after the editor does.
        /// </remarks>
        public static TileGrid FromAscii(IReadOnlyList<string> rows, float tileSize = 1f, IReadOnlyDictionary<char, byte>? legend = null)
        {
            if (rows == null)
            {
                throw new ArgumentNullException(nameof(rows));
            }

            if (rows.Count == 0)
            {
                throw new ArgumentException("empty level", nameof(rows));
            }

            legend ??= DefaultLegend;

            var height = rows.Count;
            var width = 0;
            foreach (var row in rows)
            {
                width = Math.Max(width, row.Length);
            }

            if (width == 0)
            {
                throw new ArgumentException("empty level", nameof(rows));
            }

            var grid = new TileGrid(width, height, tileSize);
            for (var line = 0; line < height; line++)
            {
                // Flip here, once: the last authored line is world row 0.
                var y = height - 1 - line;
                var text = rows[line];
                for (var x = 0; x < text.Length; x++)
                {
                    if (!legend.TryGetValue(text[x], out var tile))
                    {
                        throw new ArgumentException($"no legend entry for '{text[x]}' at line {line}, column {x}", nameof(rows));
                    }

                    grid._tiles[(y * width) + x] = tile;
                }
            }

            return grid;
        }

        /// <summary>The characters understood by <see cref="FromAscii"/> by default.</summary>
        public static IReadOnlyDictionary<char, byte> DefaultLegend { get; } = new Dictionary<char, byte>
        {
            ['.'] = Tile.Empty,
            [' '] = Tile.Empty,
            ['#'] = Tile.Solid,
            ['='] = Tile.OneWay,
            ['^'] = Tile.Hazard,
            ['G'] = Tile.Goal,
        };

        /// <summary>Whether a coordinate is inside the grid.</summary>
        public bool InBounds(int x, int y) => x >= 0 && x < Width && y >= 0 && y < Height;

        /// <summary>
        /// Reads a tile. Out of bounds returns <see cref="Tile.Solid"/> to the sides
        /// and below, and <see cref="Tile.Empty"/> above.
        /// </summary>
        /// <remarks>
        /// Treating the surround as solid means an entity can never leave the level
        /// through a wall, with no special-casing at the edges. Leaving the sky open
        /// means a jump near the ceiling is not mysteriously blocked by nothing.
        /// </remarks>
        public byte Get(int x, int y)
        {
            if (!InBounds(x, y))
            {
                return y >= Height ? Tile.Empty : Tile.Solid;
            }

            return _tiles[(y * Width) + x];
        }

        /// <summary>Writes a tile. Out-of-bounds writes are ignored.</summary>
        public void Set(int x, int y, byte tile)
        {
            if (InBounds(x, y))
            {
                _tiles[(y * Width) + x] = tile;
            }
        }

        /// <summary>Whether the tile at a coordinate blocks movement.</summary>
        public bool IsSolid(int x, int y) => SolidIds.Contains(Get(x, y));

        /// <summary>World-space bounds of one tile.</summary>
        public Aabb BoxFor(int x, int y) =>
            Aabb.FromSize(new Vector2(x * TileSize, y * TileSize), new Vector2(TileSize, TileSize));

        /// <summary>Converts a world position to the tile containing it.</summary>
        public (int X, int Y) WorldToTile(Vector2 world) =>
            ((int)Math.Floor(world.X / TileSize), (int)Math.Floor(world.Y / TileSize));

        /// <summary>Centre of a tile in world space.</summary>
        public Vector2 TileToWorld(int x, int y) =>
            new Vector2((x + 0.5f) * TileSize, (y + 0.5f) * TileSize);

        /// <summary>
        /// The inclusive grid range a world-space box covers.
        /// </summary>
        /// <remarks>
        /// The epsilon on the far edges matters: a box whose right edge sits exactly
        /// on a tile boundary does not overlap the tile beyond it, and including that
        /// column makes an entity collide with a wall it is merely flush against.
        /// </remarks>
        public (int MinX, int MinY, int MaxX, int MaxY) TileRange(Aabb box)
        {
            const float epsilon = 1e-5f;
            var minX = (int)Math.Floor(box.Min.X / TileSize);
            var minY = (int)Math.Floor(box.Min.Y / TileSize);
            var maxX = (int)Math.Floor((box.Max.X - epsilon) / TileSize);
            var maxY = (int)Math.Floor((box.Max.Y - epsilon) / TileSize);
            return (minX, minY, maxX, maxY);
        }

        /// <summary>
        /// Collision boxes for the matching tiles a box touches. This is the whole
        /// broadphase.
        /// </summary>
        /// <param name="box">Region to query, in world space.</param>
        /// <param name="tileIds">Tile ids to collect; defaults to <see cref="SolidIds"/>.</param>
        public List<Aabb> BoxesOverlapping(Aabb box, ISet<byte>? tileIds = null)
        {
            var wanted = tileIds ?? SolidIds;
            var (minX, minY, maxX, maxY) = TileRange(box);
            var result = new List<Aabb>();

            for (var y = minY; y <= maxY; y++)
            {
                for (var x = minX; x <= maxX; x++)
                {
                    if (wanted.Contains(Get(x, y)))
                    {
                        result.Add(BoxFor(x, y));
                    }
                }
            }

            return result;
        }

        /// <summary>Whether any tile the box touches is one of <paramref name="tileIds"/>.</summary>
        public bool OverlapsAny(Aabb box, ISet<byte> tileIds)
        {
            if (tileIds == null)
            {
                throw new ArgumentNullException(nameof(tileIds));
            }

            var (minX, minY, maxX, maxY) = TileRange(box);
            for (var y = minY; y <= maxY; y++)
            {
                for (var x = minX; x <= maxX; x++)
                {
                    if (tileIds.Contains(Get(x, y)))
                    {
                        return true;
                    }
                }
            }

            return false;
        }

        /// <summary>Every coordinate holding the given tile id.</summary>
        public List<(int X, int Y)> Find(byte tileId)
        {
            var found = new List<(int X, int Y)>();
            for (var i = 0; i < _tiles.Length; i++)
            {
                if (_tiles[i] == tileId)
                {
                    found.Add((i % Width, i / Width));
                }
            }

            return found;
        }

        /// <summary>Total world width.</summary>
        public float WorldWidth => Width * TileSize;

        /// <summary>Total world height.</summary>
        public float WorldHeight => Height * TileSize;

        /// <inheritdoc/>
        public override string ToString() => $"TileGrid({Width}x{Height} @ {TileSize})";
    }
}
