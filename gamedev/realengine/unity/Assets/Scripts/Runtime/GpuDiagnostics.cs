using UnityEngine;

namespace GameCore.Unity
{
    /// <summary>
    /// Logs which GPU Unity actually chose, and warns when it picked the wrong one.
    /// </summary>
    /// <remarks>
    /// <para>
    /// On a laptop with an external GPU, the most expensive failure is not a
    /// crash — it is that everything works and the frame rate is quietly bad,
    /// because Unity is rendering on the integrated GPU while the eGPU sits
    /// idle. Windows chooses a GPU per application, and its default heuristic
    /// frequently picks the integrated one for an editor process. Device Manager
    /// shows the eGPU, the enclosure lights are on, and nothing anywhere reports
    /// a problem.
    /// </para>
    /// <para>
    /// The fix is one line of diagnostics run once, at startup, before spending
    /// an afternoon optimising a scene that is slow for an entirely unrelated
    /// reason. Attach this to a bootstrap object in the first scene.
    /// </para>
    /// <para>
    /// The equivalents in the other tools, worth knowing:
    /// </para>
    /// <list type="bullet">
    /// <item><description>Unity editor: <c>Help &gt; About Unity</c> names the graphics device.</description></item>
    /// <item><description>Unreal: the log line <c>Using D3D12 adapter</c>, or <c>stat unit</c> in-game.</description></item>
    /// <item><description>Windows: Task Manager &gt; Performance, and watch which GPU's utilisation moves.</description></item>
    /// </list>
    /// </remarks>
    [DefaultExecutionOrder(-10000)]
    public sealed class GpuDiagnostics : MonoBehaviour
    {
        /// <summary>
        /// Name fragments that indicate an integrated GPU.
        /// </summary>
        /// <remarks>
        /// Matching on the marketing name is crude, but it is the only signal
        /// <see cref="SystemInfo"/> exposes. "Arc" is excluded explicitly:
        /// Intel Arc is discrete despite being an Intel part whose name contains
        /// "Graphics", and misreporting it would send someone chasing a problem
        /// they do not have.
        /// </remarks>
        private static readonly string[] IntegratedMarkers =
        {
            "uhd graphics",
            "hd graphics",
            "iris",
            "radeon(tm) graphics",
            "vega 3",
            "vega 6",
            "vega 7",
            "vega 8",
            "vega 11",
            "basic display",
            "llvmpipe",
            "software",
        };

        [Tooltip("Also log the report when running in the editor, not just in a build.")]
        [SerializeField]
        private bool logInEditor = true;

        /// <summary>The adapter Unity is rendering with.</summary>
        public static string AdapterName => SystemInfo.graphicsDeviceName;

        /// <summary>Whether the chosen adapter looks like integrated graphics.</summary>
        public static bool LooksIntegrated => IsIntegrated(SystemInfo.graphicsDeviceName);

        /// <summary>Whether a device name matches a known integrated GPU.</summary>
        /// <remarks>Public and static so an edit-mode test can cover it without a scene.</remarks>
        public static bool IsIntegrated(string deviceName)
        {
            if (string.IsNullOrEmpty(deviceName))
            {
                return false;
            }

            var lowered = deviceName.ToLowerInvariant();

            // Intel Arc is discrete; check before the generic markers.
            if (lowered.Contains("arc "))
            {
                return false;
            }

            for (var i = 0; i < IntegratedMarkers.Length; i++)
            {
                if (lowered.Contains(IntegratedMarkers[i]))
                {
                    return true;
                }
            }

            return false;
        }

        private void Awake()
        {
            if (!logInEditor && Application.isEditor)
            {
                return;
            }

            Debug.Log(
                "[GPU] " + SystemInfo.graphicsDeviceName +
                "  |  vendor: " + SystemInfo.graphicsDeviceVendor +
                "  |  VRAM: " + SystemInfo.graphicsMemorySize + " MB" +
                "  |  API: " + SystemInfo.graphicsDeviceType +
                "  |  version: " + SystemInfo.graphicsDeviceVersion);

            if (LooksIntegrated)
            {
                Debug.LogWarning(
                    "[GPU] Rendering on what looks like an INTEGRATED GPU (" +
                    SystemInfo.graphicsDeviceName + "). If an eGPU is attached it is not being used. " +
                    "Windows: Settings > System > Display > Graphics, add this executable, set " +
                    "'High performance'. Note the setting is per binary, so Unity.exe and UnityHub.exe " +
                    "must be added separately -- launching the editor from the Hub does not inherit it.");
            }
        }
    }
}
