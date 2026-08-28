#pragma once

// TileGrid — the gameplay-authoritative view of a tile level.
//
// Unreal's Paper2D has its own tile maps, and Unreal owns rendering and physics
// colliders for them. What it does not own is your gameplay's view of the level
// — line of sight, spawn rules, pathfinding, "is this tile safe to stand on" —
// and routing every one of those through the engine's collider system is both
// slow and impossible to test outside the editor. Keeping an authoritative grid
// in the core, with the engine's tilemap as a rendering mirror, is the pattern.
//
// The rule that makes tile maps viable: never iterate the whole map. A 200x60
// level is 12 000 tiles; testing an entity against all of them, sixty times a
// second, per entity, is the difference between a game and a slideshow. Because
// it is a grid, the tiles an entity could touch are found by dividing its bounds
// by the tile size, so the broadphase costs what the entity is big, not what the
// level is big.
//
// Coordinate convention: row 0 is the BOTTOM of the world, so tile (x, y) sits
// at (x * TileSize, y * TileSize) with no flip anywhere in the maths. ASCII
// levels are authored the way they look — first line is the top of the screen —
// and FromAscii reverses them on load. Doing the flip exactly once, at the
// authoring boundary, is what keeps sign errors out of gameplay code.

#include <cmath>
#include <cstddef>
#include <cstdint>
#include <vector>

#include "gamecore/Aabb.h"
#include "gamecore/Vec2.h"

namespace gamecore
{

namespace Tile
{
    constexpr uint8_t Empty = 0;
    constexpr uint8_t Solid = 1;   // blocks from every direction
    constexpr uint8_t OneWay = 2;  // blocks only from above
    constexpr uint8_t Hazard = 3;
    constexpr uint8_t Goal = 4;
} // namespace Tile

class TileGrid
{
public:
    TileGrid(int InWidth, int InHeight, float InTileSize = 1.0f)
        : Width(InWidth > 0 ? InWidth : 1)
        , Height(InHeight > 0 ? InHeight : 1)
        , TileSize(InTileSize > 0.0f ? InTileSize : 1.0f)
    {
        Tiles.assign(static_cast<size_t>(Width) * static_cast<size_t>(Height), Tile::Empty);
    }

    // Builds a grid from ASCII art where Rows[0] is the TOP line as drawn.
    // Returns false on an unknown character or empty input; no exceptions, so
    // the caller checks the return value.
    static bool FromAscii(const char* const* Rows, int RowCount, float TileSize, TileGrid& OutGrid);

    int GetWidth() const { return Width; }
    int GetHeight() const { return Height; }
    float GetTileSize() const { return TileSize; }

    bool InBounds(int X, int Y) const { return X >= 0 && X < Width && Y >= 0 && Y < Height; }

    // Out of bounds reads as Solid to the sides and below, Empty above.
    // Solid surroundings mean an entity can never leave the level through a wall
    // with no edge special-casing; open sky means a jump near the ceiling is not
    // mysteriously blocked by nothing.
    uint8_t Get(int X, int Y) const
    {
        if (!InBounds(X, Y))
        {
            return Y >= Height ? Tile::Empty : Tile::Solid;
        }
        return Tiles[static_cast<size_t>(Y) * static_cast<size_t>(Width) + static_cast<size_t>(X)];
    }

    void Set(int X, int Y, uint8_t Value)
    {
        if (InBounds(X, Y))
        {
            Tiles[static_cast<size_t>(Y) * static_cast<size_t>(Width) + static_cast<size_t>(X)] = Value;
        }
    }

    bool IsSolid(int X, int Y) const { return Get(X, Y) == Tile::Solid; }

    Aabb BoxFor(int X, int Y) const
    {
        return Aabb::FromSize(
            Vec2(static_cast<float>(X) * TileSize, static_cast<float>(Y) * TileSize),
            Vec2(TileSize, TileSize));
    }

    // Flooring, not truncation. Truncation towards zero maps -0.5 and +0.5 to the
    // same tile, which puts an entity standing just left of the origin in the
    // wrong cell.
    void WorldToTile(const Vec2& World, int& OutX, int& OutY) const
    {
        OutX = static_cast<int>(std::floor(World.X / TileSize));
        OutY = static_cast<int>(std::floor(World.Y / TileSize));
    }

    Vec2 TileToWorld(int X, int Y) const
    {
        return Vec2((static_cast<float>(X) + 0.5f) * TileSize, (static_cast<float>(Y) + 0.5f) * TileSize);
    }

    // The inclusive grid range a world-space box covers. The epsilon on the far
    // edges matters: a box whose right edge sits exactly on a tile boundary does
    // not overlap the tile beyond it, and including that column makes an entity
    // collide with a wall it is merely flush against.
    void TileRange(const Aabb& Box, int& OutMinX, int& OutMinY, int& OutMaxX, int& OutMaxY) const
    {
        constexpr float Epsilon = 1e-5f;
        OutMinX = static_cast<int>(std::floor(Box.Min.X / TileSize));
        OutMinY = static_cast<int>(std::floor(Box.Min.Y / TileSize));
        OutMaxX = static_cast<int>(std::floor((Box.Max.X - Epsilon) / TileSize));
        OutMaxY = static_cast<int>(std::floor((Box.Max.Y - Epsilon) / TileSize));
    }

    // The whole broadphase. Appends to OutBoxes rather than returning a
    // container, so a caller can reuse one buffer across every entity and every
    // frame instead of allocating per query — the same reason Unreal's own query
    // API fills a TArray you own.
    void BoxesOverlapping(const Aabb& Box, uint8_t WantedTile, std::vector<Aabb>& OutBoxes) const
    {
        int MinX, MinY, MaxX, MaxY;
        TileRange(Box, MinX, MinY, MaxX, MaxY);

        for (int Y = MinY; Y <= MaxY; ++Y)
        {
            for (int X = MinX; X <= MaxX; ++X)
            {
                if (Get(X, Y) == WantedTile)
                {
                    OutBoxes.push_back(BoxFor(X, Y));
                }
            }
        }
    }

    bool OverlapsAny(const Aabb& Box, uint8_t WantedTile) const
    {
        int MinX, MinY, MaxX, MaxY;
        TileRange(Box, MinX, MinY, MaxX, MaxY);

        for (int Y = MinY; Y <= MaxY; ++Y)
        {
            for (int X = MinX; X <= MaxX; ++X)
            {
                if (Get(X, Y) == WantedTile)
                {
                    return true;
                }
            }
        }
        return false;
    }

    float WorldWidth() const { return static_cast<float>(Width) * TileSize; }
    float WorldHeight() const { return static_cast<float>(Height) * TileSize; }

private:
    int Width = 1;
    int Height = 1;
    float TileSize = 1.0f;
    std::vector<uint8_t> Tiles;
};

} // namespace gamecore
