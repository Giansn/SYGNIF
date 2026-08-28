// A minimal stand-in for the parts of UnityEngine the adapters touch.
//
// WHAT THIS IS FOR
// Unity cannot be installed in this environment: the editor needs a licensed
// GUI install and several gigabytes. That would normally mean the MonoBehaviour
// adapters are unverifiable — written, committed, and only discovered to be
// wrong when someone opens the project.
//
// Compiling them against a shim closes most of that gap. It proves the adapters
// parse, that every type and member they reference exists with the signature
// they assume, that the overload resolution works, and that the core library
// genuinely links against engine-facing code. A typo in a method name, a
// Vector2/Vector3 mix-up, or a missing using is caught here in two seconds.
//
// WHAT THIS IS NOT
// It proves nothing about behaviour. Unity's lifecycle callbacks, execution
// order, serialisation of [SerializeField] fields, physics, and the actual
// semantics of Time.fixedDeltaTime are all real-engine concerns that only run
// in the editor. Treat a green build here as "this will compile when opened",
// not "this works".
//
// The shim deliberately mirrors Unity's real signatures, including the
// lowercase field names on Vector2/Vector3 and the implicit conversions between
// them, because those are exactly the details an adapter can get wrong.

using System;

namespace UnityEngine
{
    public struct Vector2
    {
        public float x;
        public float y;

        public Vector2(float x, float y)
        {
            this.x = x;
            this.y = y;
        }

        public static Vector2 operator +(Vector2 a, Vector2 b) => new Vector2(a.x + b.x, a.y + b.y);

        public static Vector2 operator -(Vector2 a, Vector2 b) => new Vector2(a.x - b.x, a.y - b.y);

        public static Vector2 operator *(Vector2 a, float d) => new Vector2(a.x * d, a.y * d);

        // Unity defines these implicitly in both directions, and the Z-dropping
        // direction is a routine source of bugs in real code, so the shim has to
        // reproduce it or it would hide them.
        public static implicit operator Vector3(Vector2 v) => new Vector3(v.x, v.y, 0f);

        public static implicit operator Vector2(Vector3 v) => new Vector2(v.x, v.y);
    }

    public struct Vector3
    {
        public float x;
        public float y;
        public float z;

        public Vector3(float x, float y, float z)
        {
            this.x = x;
            this.y = y;
            this.z = z;
        }

        public static Vector3 operator +(Vector3 a, Vector3 b) => new Vector3(a.x + b.x, a.y + b.y, a.z + b.z);

        public static Vector3 operator -(Vector3 a, Vector3 b) => new Vector3(a.x - b.x, a.y - b.y, a.z - b.z);
    }

    public struct Color
    {
        public float r, g, b, a;

        public Color(float r, float g, float b, float a)
        {
            this.r = r;
            this.g = g;
            this.b = b;
            this.a = a;
        }

        public static Color green => new Color(0f, 1f, 0f, 1f);
        public static Color red => new Color(1f, 0f, 0f, 1f);
    }

    public static class Mathf
    {
        public static float Abs(float value) => Math.Abs(value);
        public static float Clamp(float value, float min, float max) => value < min ? min : (value > max ? max : value);
        public static float Sign(float value) => value < 0f ? -1f : 1f;
    }

    public class Object
    {
        public string name = string.Empty;
    }

    public class Transform : Component
    {
        public Vector3 position;
        public Vector3 localScale;
    }

    public class Component : Object
    {
        public Transform transform = new Transform();
        public GameObject gameObject = new GameObject();
    }

    public class GameObject : Object
    {
        public T GetComponent<T>() where T : class => null;
    }

    public class Behaviour : Component
    {
        public bool enabled = true;
    }

    public class MonoBehaviour : Behaviour
    {
    }

    public class ScriptableObject : Object
    {
    }

    public static class Time
    {
        public static float deltaTime => 1f / 60f;
        public static float fixedDeltaTime => 1f / 50f;
        public static float time => 0f;

        // Unity's own clamp on accumulated frame time — the spiral-of-death guard.
        public static float maximumDeltaTime => 1f / 3f;
    }

    public enum KeyCode
    {
        None = 0,
        Space = 32,
        A = 97,
        D = 100,
        S = 115,
        W = 119,
        LeftArrow = 276,
        RightArrow = 275,
        UpArrow = 273,
        DownArrow = 274,
    }

    public static class Input
    {
        public static bool GetKey(KeyCode key) => false;
        public static bool GetKeyDown(KeyCode key) => false;
        public static bool GetKeyUp(KeyCode key) => false;
        public static float GetAxisRaw(string axisName) => 0f;
    }

    public static class Gizmos
    {
        public static Color color;
        public static void DrawWireCube(Vector3 center, Vector3 size) { }
        public static void DrawLine(Vector3 from, Vector3 to) { }
    }

    public static class Debug
    {
        public static void Log(object message) { }
        public static void LogWarning(object message) { }
        public static void LogError(object message) { }
    }

    [AttributeUsage(AttributeTargets.Field)]
    public sealed class SerializeField : Attribute
    {
    }

    [AttributeUsage(AttributeTargets.Field)]
    public sealed class TooltipAttribute : Attribute
    {
        public TooltipAttribute(string tooltip) { }
    }

    [AttributeUsage(AttributeTargets.Field)]
    public sealed class HeaderAttribute : Attribute
    {
        public HeaderAttribute(string header) { }
    }

    [AttributeUsage(AttributeTargets.Field)]
    public sealed class RangeAttribute : Attribute
    {
        public RangeAttribute(float min, float max) { }
    }

    [AttributeUsage(AttributeTargets.Class)]
    public sealed class DefaultExecutionOrder : Attribute
    {
        public DefaultExecutionOrder(int order) { }
    }

    [AttributeUsage(AttributeTargets.Class, AllowMultiple = true)]
    public sealed class RequireComponent : Attribute
    {
        public RequireComponent(Type requiredComponent) { }
    }
}
