using System.Collections.Generic;
using GameCore.Geometry;
using GameCore.Tiles;
using UnityEngine;

namespace GameCore.Unity
{
    /// <summary>
    /// Moves a transform through a <see cref="TileGrid"/> using the core's swept
    /// collision, with no Unity physics involved.
    /// </summary>
    /// <remarks>
    /// <para>
    /// <b>Why not just use Rigidbody2D.</b> For most games you should — Unity's
    /// physics is fast, well tested and handles far more than boxes on a grid. This
    /// exists for the cases where it is the wrong tool, and those cases are
    /// specific and common:
    /// </para>
    /// <list type="bullet">
    /// <item><description>
    /// <b>Precise platformer feel.</b> A dynamic rigidbody is a simulation of a
    /// physical object, and a good platformer character is not one: it accelerates
    /// instantly, stops dead, and has hand-tuned gravity that differs going up and
    /// coming down. Fighting the solver to get that is harder than not using it.
    /// </description></item>
    /// <item><description>
    /// <b>Determinism.</b> Box2D and PhysX are not bit-reproducible across
    /// platforms and are stateful in ways a test cannot easily set up. Anything
    /// driving replays, deterministic tests or lockstep networking has to move
    /// outside them.
    /// </description></item>
    /// </list>
    /// <para>
    /// <b>The trap this component is written to avoid.</b> Unity's default
    /// <c>Collision Detection</c> on a rigidbody is <c>Discrete</c>: it asks "do
    /// these overlap right now". A bullet that was in front of a wall on one tick
    /// and behind it on the next never overlaps at any sampled instant, so it
    /// passes straight through — the single most-filed bug in every project. The
    /// core's <see cref="Sweep.MoveAndSlide"/> asks the continuous question
    /// instead: over the course of this motion, when did contact first occur.
    /// </para>
    /// <para>
    /// Movement happens in <c>FixedUpdate</c> so the step size is constant, and the
    /// transform is written once at the end. Writing the transform repeatedly
    /// inside the resolution loop would be both slower and visible as jitter.
    /// </para>
    /// </remarks>
    [DefaultExecutionOrder(-500)]
    public sealed class KinematicMover : MonoBehaviour
    {
        [Tooltip("Collision box size in world units, centred on the transform.")]
        [SerializeField]
        private Vector2 size = new Vector2(0.8f, 1.8f);

        [Tooltip("Extra gap kept at contact, in world units. Landing exactly flush " +
                 "makes 'am I grounded' flicker with floating point noise.")]
        [SerializeField]
        private float skin = 0.001f;

        private TileGrid grid;

        // Reused across frames so a moving character does not allocate a list per
        // tick. Per-frame allocation is what turns into periodic GC stutter, and it
        // is the most common avoidable performance defect in a Unity project.
        private readonly List<Aabb> obstacleBuffer = new List<Aabb>();
        private readonly List<SweepHit> hitBuffer = new List<SweepHit>();

        /// <summary>Whether the last move ended with ground underfoot.</summary>
        public bool IsGrounded { get; private set; }

        /// <summary>Whether the last move struck a wall.</summary>
        public bool IsTouchingWall { get; private set; }

        /// <summary>Supplies the level this mover collides against.</summary>
        public void SetGrid(TileGrid tileGrid) => grid = tileGrid;

        /// <summary>The mover's collision bounds at its current position.</summary>
        public Aabb Bounds =>
            Aabb.FromCenter(NumericsInterop.ToCore(transform.position), NumericsInterop.ToCore(size));

        /// <summary>
        /// Moves by <paramref name="displacement"/> world units, sliding along
        /// contacts, and returns the motion actually achieved.
        /// </summary>
        /// <remarks>
        /// The caller passes a displacement (velocity already multiplied by the
        /// timestep) rather than a velocity, so this component never needs an
        /// opinion about what the timestep is.
        /// </remarks>
        public Vector2 Move(Vector2 displacement)
        {
            IsGrounded = false;
            IsTouchingWall = false;

            if (grid == null)
            {
                transform.position += (Vector3)displacement;
                return displacement;
            }

            var box = Bounds;
            var motion = NumericsInterop.ToCore(displacement);

            // Broadphase: gather only the tiles along the path. Querying the swept
            // bounds rather than the current bounds is what stops a fast mover from
            // missing tiles it flies over within a single step.
            obstacleBuffer.Clear();
            obstacleBuffer.AddRange(grid.BoxesOverlapping(box.SweptBounds(motion)));

            hitBuffer.Clear();
            var moved = Sweep.MoveAndSlide(box, motion, obstacleBuffer, hitBuffer, skin: skin);

            for (var i = 0; i < hitBuffer.Count; i++)
            {
                var normal = hitBuffer[i].Normal;
                // Y-up: a normal pointing up means the surface is below us.
                if (normal.Y > 0.5f)
                {
                    IsGrounded = true;
                }
                else if (Mathf.Abs(normal.X) > 0.5f)
                {
                    IsTouchingWall = true;
                }
            }

            transform.position += (Vector3)NumericsInterop.ToUnity(moved);
            return NumericsInterop.ToUnity(moved);
        }

        private void OnDrawGizmosSelected()
        {
            // Seeing the collision box is worth the six lines. A box that does not
            // match the sprite is invisible in play mode and obvious here.
            Gizmos.color = Color.green;
            Gizmos.DrawWireCube(transform.position, new Vector3(size.x, size.y, 0f));
        }
    }
}
