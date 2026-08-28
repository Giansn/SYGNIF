using UnityEngine;

namespace GameCore.Unity
{
    /// <summary>
    /// Conversions between the core's <see cref="System.Numerics.Vector2"/> and
    /// Unity's <see cref="Vector2"/>.
    /// </summary>
    /// <remarks>
    /// <para>
    /// Two vector types in one codebase looks like an obvious wart, and the
    /// temptation is to delete the core's type and use Unity's everywhere. Doing
    /// that is what makes gameplay logic untestable: <c>UnityEngine.Vector2</c>
    /// lives in UnityEngine.dll, so any file that mentions it can only be compiled
    /// by the editor, which means the only way to run a test over it is to enter
    /// play mode. Keeping the boundary costs two conversion functions and buys a
    /// test suite that runs in 125 ms from a terminal.
    /// </para>
    /// <para>
    /// The conversions are deliberately explicit rather than implicit operators.
    /// An implicit conversion would let engine types leak into core code by
    /// accident and compile silently, which is exactly the erosion this whole
    /// arrangement exists to prevent — the conversion should be a visible
    /// crossing of a boundary, not an invisible one.
    /// </para>
    /// <para>
    /// The Z axis is dropped when converting from <see cref="Vector3"/>, which is
    /// correct for a 2D game where Z is only ever the sprite sorting depth.
    /// </para>
    /// </remarks>
    public static class NumericsInterop
    {
        /// <summary>Core vector to Unity vector.</summary>
        public static Vector2 ToUnity(System.Numerics.Vector2 value) => new Vector2(value.X, value.Y);

        /// <summary>Core vector to a Unity 3D vector at the given depth.</summary>
        public static Vector3 ToUnity(System.Numerics.Vector2 value, float z) => new Vector3(value.X, value.Y, z);

        /// <summary>Unity vector to core vector.</summary>
        public static System.Numerics.Vector2 ToCore(Vector2 value) => new System.Numerics.Vector2(value.x, value.y);

        /// <summary>Unity 3D vector to core vector, discarding depth.</summary>
        public static System.Numerics.Vector2 ToCore(Vector3 value) => new System.Numerics.Vector2(value.x, value.y);
    }
}
