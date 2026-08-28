using System;
using System.Collections.Generic;

namespace GameCore.Input
{
    /// <summary>
    /// Tracks which named actions are held, and which changed on this tick.
    /// </summary>
    /// <remarks>
    /// <para>
    /// Gameplay needs three different questions answered about a button, and
    /// conflating them is a rich source of bugs: <em>is it held</em> (walking),
    /// <em>was it just pressed</em> (jumping, which must fire exactly once), and
    /// <em>was it just released</em> (variable-height jumps, charged attacks).
    /// Only the first is observable from hardware; the other two are derived by
    /// comparing this tick against the last, so something must own that history.
    /// </para>
    /// <para>
    /// <b>The Unity trap this exists for.</b> <c>Input.GetKeyDown</c> and the new
    /// Input System's <c>WasPressedThisFrame</c> are keyed to the <em>render
    /// frame</em>. <c>FixedUpdate</c> does not run once per frame — it runs zero,
    /// one or several times, driven by the same accumulator as
    /// <see cref="Time.FixedTimestepAccumulator"/>. So reading
    /// <c>GetKeyDown</c> inside <c>FixedUpdate</c> is doubly broken: a press can be
    /// missed entirely when a frame runs no fixed ticks, and can fire several times
    /// when a frame runs several. The documented fix is to sample input in
    /// <c>Update</c> and consume it in <c>FixedUpdate</c> — which means latching it
    /// somewhere, which is this class.
    /// </para>
    /// <para>
    /// <b>The second trap: losing a tap shorter than one tick.</b> At 60 Hz a tick
    /// is 16 ms; a fast player, or a 1000 Hz gaming mouse, can press and release
    /// inside that window. Comparing before/after snapshots shows the button down at
    /// neither end and the input silently vanishes. Presses are therefore
    /// <em>latched</em> when they arrive and cleared only when consumed, so a press
    /// is never dropped.
    /// </para>
    /// <para>
    /// Actions are named strings rather than key codes so gameplay talks about
    /// "Jump" rather than "Space". That indirection is what makes rebinding,
    /// gamepads and replay files possible without touching gameplay code, and it is
    /// the same idea as Unreal's Enhanced Input actions or Unity's Input Actions.
    /// </para>
    /// </remarks>
    public sealed class ActionState
    {
        private readonly HashSet<string> _held = new HashSet<string>();
        private readonly HashSet<string> _previous = new HashSet<string>();
        private readonly HashSet<string> _pressedSinceTick = new HashSet<string>();
        private readonly HashSet<string> _releasedSinceTick = new HashSet<string>();
        private readonly HashSet<string> _justPressed = new HashSet<string>();
        private readonly HashSet<string> _justReleased = new HashSet<string>();

        /// <summary>Records that an action went down. Safe to call at any time.</summary>
        public void Press(string action)
        {
            if (action == null)
            {
                throw new ArgumentNullException(nameof(action));
            }

            _held.Add(action);
            _pressedSinceTick.Add(action);
        }

        /// <summary>Records that an action went up. Safe to call at any time.</summary>
        public void Release(string action)
        {
            if (action == null)
            {
                throw new ArgumentNullException(nameof(action));
            }

            _held.Remove(action);
            _releasedSinceTick.Add(action);
        }

        /// <summary>Sets the held state of an action directly.</summary>
        public void SetHeld(string action, bool down)
        {
            if (down)
            {
                Press(action);
            }
            else
            {
                Release(action);
            }
        }

        /// <summary>
        /// Drops all state, for when the window loses focus.
        /// </summary>
        /// <remarks>
        /// Without this, alt-tabbing away mid-stride leaves the movement key stuck
        /// down forever and the character walks into a wall on return. In Unity this
        /// belongs in <c>OnApplicationFocus(false)</c>.
        /// </remarks>
        public void Clear()
        {
            _held.Clear();
            _previous.Clear();
            _pressedSinceTick.Clear();
            _releasedSinceTick.Clear();
            _justPressed.Clear();
            _justReleased.Clear();
        }

        /// <summary>
        /// Recomputes the edges. Call exactly once per <em>simulation tick</em>,
        /// not once per rendered frame.
        /// </summary>
        public void BeginTick()
        {
            _justPressed.Clear();
            foreach (var action in _held)
            {
                if (!_previous.Contains(action))
                {
                    _justPressed.Add(action);
                }
            }

            // A tap that went down and back up inside one tick still counts.
            foreach (var action in _pressedSinceTick)
            {
                _justPressed.Add(action);
            }

            _justReleased.Clear();
            foreach (var action in _previous)
            {
                if (!_held.Contains(action))
                {
                    _justReleased.Add(action);
                }
            }

            foreach (var action in _releasedSinceTick)
            {
                if (!_held.Contains(action))
                {
                    _justReleased.Add(action);
                }
            }

            _previous.Clear();
            foreach (var action in _held)
            {
                _previous.Add(action);
            }

            _pressedSinceTick.Clear();
            _releasedSinceTick.Clear();
        }

        /// <summary>Whether the action is currently down.</summary>
        public bool IsHeld(string action) => _held.Contains(action);

        /// <summary>Whether the action went down during this tick.</summary>
        public bool JustPressed(string action) => _justPressed.Contains(action);

        /// <summary>Whether the action went up during this tick.</summary>
        public bool JustReleased(string action) => _justReleased.Contains(action);

        /// <summary>
        /// A -1 / 0 / +1 axis from two opposed actions.
        /// </summary>
        /// <remarks>
        /// Holding both yields zero rather than favouring one. Picking a winner
        /// produces the notorious bug where tapping left while running right makes
        /// the character sprint the wrong way.
        /// </remarks>
        public float Axis(string negative, string positive)
        {
            var value = 0f;
            if (_held.Contains(positive))
            {
                value += 1f;
            }

            if (_held.Contains(negative))
            {
                value -= 1f;
            }

            return value;
        }
    }
}
