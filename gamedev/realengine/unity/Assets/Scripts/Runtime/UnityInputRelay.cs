using System;
using System.Collections.Generic;
using GameCore.Input;
using UnityEngine;

namespace GameCore.Unity
{
    /// <summary>
    /// Samples Unity input in <c>Update</c> and hands it to the core's
    /// <see cref="ActionState"/> for consumption in <c>FixedUpdate</c>.
    /// </summary>
    /// <remarks>
    /// <para>
    /// This component exists because of one of Unity's sharpest edges, and it is
    /// worth stating precisely.
    /// </para>
    /// <para>
    /// <c>Input.GetKeyDown</c> is true for the duration of the <em>render frame</em>
    /// in which the key went down. <c>FixedUpdate</c> is not tied to render
    /// frames: it is driven by an accumulator over <c>Time.fixedDeltaTime</c>, so
    /// in any given frame it runs zero, one, or several times. That produces two
    /// failure modes, and both are intermittent, which is the worst kind:
    /// </para>
    /// <list type="bullet">
    /// <item><description>
    /// On a fast machine, frames are shorter than the fixed step, so some frames
    /// run <em>no</em> fixed tick at all. A key pressed and released inside such a
    /// frame is never observed by <c>FixedUpdate</c> and the jump is silently
    /// dropped. Players report this as "the controls miss inputs sometimes".
    /// </description></item>
    /// <item><description>
    /// On a slow machine, one frame can run several fixed ticks, and
    /// <c>GetKeyDown</c> stays true for all of them, so one tap on jump becomes
    /// three jumps.
    /// </description></item>
    /// </list>
    /// <para>
    /// The documented fix is to read input in <c>Update</c> and consume it in
    /// <c>FixedUpdate</c>, which requires somewhere to latch it in between. That
    /// is <see cref="ActionState"/>: presses accumulate as they arrive, and
    /// <c>BeginTick</c> converts them into edges exactly once per simulation tick.
    /// </para>
    /// <para>
    /// Execution order matters. This component must run before anything that reads
    /// the input, which is what the negative <c>DefaultExecutionOrder</c> asks for.
    /// Relying on the default (undefined) order is a real bug: it works until
    /// someone adds a script and the ordering silently changes.
    /// </para>
    /// <para>
    /// This uses the legacy <c>Input</c> class deliberately. The newer Input System
    /// package is the better choice for a real project — rebinding, multiple
    /// devices, and event-driven callbacks that fire independently of the frame —
    /// but it is a package dependency, and the trap being demonstrated here is
    /// identical in both. Under the new system the same latching applies; you would
    /// subscribe to <c>action.performed</c> and call <see cref="ActionState.Press"/>
    /// from the callback.
    /// </para>
    /// </remarks>
    [DefaultExecutionOrder(-1000)]
    public sealed class UnityInputRelay : MonoBehaviour
    {
        /// <summary>Maps a named gameplay action to a physical key.</summary>
        [Serializable]
        public struct Binding
        {
            /// <summary>The gameplay action name, e.g. "Jump".</summary>
            public string Action;

            /// <summary>The key that triggers it.</summary>
            public KeyCode Key;
        }

        [Tooltip("Action-to-key bindings. Names are what gameplay code asks for, " +
                 "so rebinding never touches gameplay code.")]
        [SerializeField]
        private List<Binding> bindings = new List<Binding>
        {
            new Binding { Action = "Left", Key = KeyCode.A },
            new Binding { Action = "Right", Key = KeyCode.D },
            new Binding { Action = "Jump", Key = KeyCode.Space },
        };

        /// <summary>The latched input state, consumed by the simulation.</summary>
        public ActionState State { get; } = new ActionState();

        private void Update()
        {
            // Sampled here, in the frame callback, because this is the only place
            // Unity's per-frame edge flags are meaningful.
            for (var i = 0; i < bindings.Count; i++)
            {
                var binding = bindings[i];
                if (string.IsNullOrEmpty(binding.Action))
                {
                    continue;
                }

                // GetKeyDown rather than GetKey, so a press that starts and ends
                // between two fixed ticks still registers as a press. ActionState
                // latches it until a tick consumes it.
                if (UnityEngine.Input.GetKeyDown(binding.Key))
                {
                    State.Press(binding.Action);
                }

                if (UnityEngine.Input.GetKeyUp(binding.Key))
                {
                    State.Release(binding.Action);
                }
            }
        }

        /// <summary>
        /// Rolls the input edges. Call this once at the top of the simulation tick,
        /// before anything reads the state.
        /// </summary>
        /// <remarks>
        /// Exposed rather than done in this component's own <c>FixedUpdate</c>
        /// because "exactly once per tick, before every reader" cannot be
        /// guaranteed by execution order alone once several systems read input. The
        /// simulation driver owning the call makes the ordering explicit.
        /// </remarks>
        public void BeginSimulationTick() => State.BeginTick();

        private void OnApplicationFocus(bool hasFocus)
        {
            // Without this, alt-tabbing away mid-stride leaves the movement key
            // stuck down forever and the character walks into a wall on return.
            // Unity delivers no key-up for a key released while unfocused.
            if (!hasFocus)
            {
                State.Clear();
            }
        }

        private void OnDisable() => State.Clear();
    }
}
