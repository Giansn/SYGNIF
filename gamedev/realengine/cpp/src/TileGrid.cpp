#include "gamecore/TileGrid.h"

#include <cstring>

namespace gamecore
{

namespace
{
    bool LegendLookup(char Character, uint8_t& OutTile)
    {
        switch (Character)
        {
            case '.':
            case ' ':
                OutTile = Tile::Empty;
                return true;
            case '#':
                OutTile = Tile::Solid;
                return true;
            case '=':
                OutTile = Tile::OneWay;
                return true;
            case '^':
                OutTile = Tile::Hazard;
                return true;
            case 'G':
                OutTile = Tile::Goal;
                return true;
            default:
                return false;
        }
    }
} // namespace

bool TileGrid::FromAscii(const char* const* Rows, int RowCount, float TileSize, TileGrid& OutGrid)
{
    if (Rows == nullptr || RowCount <= 0 || TileSize <= 0.0f)
    {
        return false;
    }

    int MaxWidth = 0;
    for (int Line = 0; Line < RowCount; ++Line)
    {
        if (Rows[Line] == nullptr)
        {
            return false;
        }

        const int Length = static_cast<int>(std::strlen(Rows[Line]));
        if (Length > MaxWidth)
        {
            MaxWidth = Length;
        }
    }

    if (MaxWidth == 0)
    {
        return false;
    }

    TileGrid Grid(MaxWidth, RowCount, TileSize);

    for (int Line = 0; Line < RowCount; ++Line)
    {
        // The flip, done exactly once: the last authored line is world row 0.
        const int Y = RowCount - 1 - Line;
        const char* Text = Rows[Line];

        for (int X = 0; Text[X] != '\0'; ++X)
        {
            uint8_t TileValue = Tile::Empty;
            if (!LegendLookup(Text[X], TileValue))
            {
                return false;
            }
            Grid.Set(X, Y, TileValue);
        }
    }

    OutGrid = Grid;
    return true;
}

} // namespace gamecore
