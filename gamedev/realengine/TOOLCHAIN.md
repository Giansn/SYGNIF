# Toolchain: what can and cannot be verified here

This environment is a headless Linux container. Neither engine can be installed
in it, so this file states exactly what is proven, what stands in for the rest,
and how to reproduce all of it.

## Installed and working

| Tool | Version | Used for |
|---|---|---|
| Python | 3.11.15 | the from-scratch engine in `gamedev/` |
| .NET SDK | 8.0.424 | builds `netstandard2.1`, Unity's scripting target |
| g++ | 13.3.0 | the C++ core, Unreal build settings |
| clang++ | 18.1.3 | second compiler, to catch compiler-specific assumptions |
| CMake | 3.28.3 | C++ build and `ctest` |
| Ninja | 1.11.1 | build backend |

.NET was not preinstalled. To reproduce:

```bash
curl -sSL https://dot.net/v1/dotnet-install.sh -o dotnet-install.sh
bash dotnet-install.sh --channel 8.0 --install-dir "$HOME/.dotnet" --no-path
export DOTNET_ROOT="$HOME/.dotnet"
export PATH="$DOTNET_ROOT:$PATH"
```

## Not installable here

| Tool | Why not | What stands in |
|---|---|---|
| Unity Editor | Licensed install, GUI-driven Hub, several GB | `unity/ci/UnityShim` compiles the adapters against a hand-written `UnityEngine` shim |
| Unreal Engine | Source build is tens of GB and hours; Epic Launcher is GUI-only | `unreal/ci/UnrealShim` compiles the module against a hand-written Unreal shim |

## What the shims do and do not prove

**Do prove.** The adapter code parses. Every type and member it references
exists with the signature it assumes. Overload resolution succeeds. The
engine-free core genuinely links against engine-facing code. The Unreal module
is exception-free and RTTI-free, because it is compiled with
`-fno-exceptions -fno-rtti` exactly as a game module is.

**Do not prove.** Anything about engine behaviour. Specifically:

- Unity: lifecycle callback order, `[SerializeField]` serialisation, prefab and
  scene behaviour, the actual semantics of `Time.fixedDeltaTime`.
- Unreal: everything behind UnrealHeaderTool — reflection, Blueprint exposure,
  replication, property serialisation, garbage collection. `UCLASS`,
  `UPROPERTY` and `UFUNCTION` are erased to nothing by the shim, so a mistake
  only UHT would catch passes silently.

Treat a green shim build as *"this will compile when someone opens the
project"*, not *"this works"*.

## Running everything

```bash
gamedev/realengine/verify.sh
```

Runs five checks and prints a summary:

| Step | What it runs | Current |
|---|---|---|
| Python core | `python3 -m unittest discover` | 140 tests |
| C# core | `dotnet test` | 68 tests, ~150 ms |
| Unity adapters | `dotnet build` against the shim | compiles |
| C++ core | `cmake --build` + test binary | 38 tests, 1542 checks |
| Unreal module | `cmake --build` against the shim | compiles |

Individually:

```bash
# Python
python3 -m unittest discover -s gamedev/tests -t .

# C#
dotnet test gamedev/realengine/csharp/GameCore.sln

# C++ core, with ctest
cmake -S gamedev/realengine/cpp -B gamedev/realengine/cpp/build
cmake --build gamedev/realengine/cpp/build
ctest --test-dir gamedev/realengine/cpp/build --output-on-failure

# Same sources under a second compiler
cmake -S gamedev/realengine/cpp -B /tmp/gc-clang -DCMAKE_CXX_COMPILER=clang++
cmake --build /tmp/gc-clang && /tmp/gc-clang/gamecore_tests
```

## Build settings that are deliberate

**C# (`GameCore.csproj`)** — `netstandard2.1` because that is Unity's API
compatibility level from 2021.2; targeting `net8.0` would build fine here and
fail the moment the DLL lands in `Assets/`. `LangVersion 9.0` because Unity's
Roslyn ships C# 9, so file-scoped namespaces and required members must fail here
rather than surprise you in the editor. `TreatWarningsAsErrors`.

**C++ (`CMakeLists.txt`)** — `-fno-exceptions -fno-rtti` to match an Unreal game
module's defaults, plus `-Werror` with `-Wconversion`, `-Wfloat-equal`,
`-Wshadow`, `-Wsign-conversion` and `-Wold-style-cast`. These caught real
defects: implicit `int`→`float` conversions across the tile grid, and a
portability bug where RNG draws written inline as constructor arguments depended
on C++'s unspecified argument evaluation order.

Both compilers are run because a single compiler lets compiler-specific
assumptions through. g++ and clang++ report identical check counts, which is
what confirms the evaluation-order fix actually worked.

## Why there is no GoogleTest

Unreal game modules build with exceptions disabled. A test framework that
expects exceptions would either force them back on — making the build no longer
representative of an engine build — or need enough configuration that the
dependency stops paying for itself. `tests/TinyTest.h` is about 60 lines and
counts failures rather than throwing.

For a real Unreal project the in-engine equivalent is the Automation Testing
framework, runnable headlessly via
`UnrealEditor-Cmd -ExecCmds="Automation RunTests ..." -unattended`. That runs
the engine; this runs in ten milliseconds, which is what you want for pure
gameplay logic.
