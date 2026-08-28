#!/usr/bin/env bash
# Run everything that can actually be verified in this environment.
#
# Neither Unity nor Unreal can be installed here — Unity needs a licensed GUI
# install, Unreal a source build measured in tens of gigabytes and hours. What
# CAN be verified is the part that matters most and is easiest to get wrong: the
# gameplay logic itself, plus whether the engine-facing adapter code compiles.
#
#   Python core      the original from-scratch engine (gamedev/)
#   C# core          Unity's scripting target, netstandard2.1 + C# 9
#   Unity adapters   compiled against a UnityEngine shim (types, not behaviour)
#   C++ core         Unreal's build settings: -fno-exceptions -fno-rtti -Werror
#
# What is deliberately NOT claimed: that anything behaves correctly inside an
# editor. Lifecycle order, serialisation, physics and asset loading are real
# engine concerns and are listed as open items in LEARNING_PATH.md.

set -uo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
realengine="$repo_root/gamedev/realengine"

export DOTNET_ROOT="${DOTNET_ROOT:-$HOME/.dotnet}"
export PATH="$DOTNET_ROOT:$PATH"
export DOTNET_CLI_TELEMETRY_OPTOUT=1
export DOTNET_NOLOGO=1

failures=0
declare -a results=()

step() {
    local name="$1"
    shift
    printf '\n\033[1m== %s ==\033[0m\n' "$name"
    if "$@"; then
        results+=("PASS  $name")
    else
        results+=("FAIL  $name")
        failures=$((failures + 1))
    fi
}

run_python_tests() {
    (cd "$repo_root" && python3 -m unittest discover -s gamedev/tests -t . 2>&1 | tail -4)
    return "${PIPESTATUS[0]:-0}"
}

run_csharp_tests() {
    command -v dotnet >/dev/null 2>&1 || { echo "dotnet not installed; see TOOLCHAIN.md"; return 1; }
    dotnet test "$realengine/csharp/GameCore.sln" --nologo -v q 2>&1 | tail -4
}

run_unity_shim_check() {
    command -v dotnet >/dev/null 2>&1 || { echo "dotnet not installed; see TOOLCHAIN.md"; return 1; }
    dotnet build "$realengine/unity/ci/UnityShim/UnityShim.csproj" --nologo -v q 2>&1 | tail -4
}

run_cpp_tests() {
    command -v cmake >/dev/null 2>&1 || { echo "cmake not installed; see TOOLCHAIN.md"; return 1; }
    cmake -S "$realengine/cpp" -B "$realengine/cpp/build" -DCMAKE_BUILD_TYPE=RelWithDebInfo >/dev/null 2>&1 || return 1
    cmake --build "$realengine/cpp/build" >/dev/null 2>&1 || return 1
    "$realengine/cpp/build/gamecore_tests" | tail -2
}

step "Python core (from-scratch engine)" run_python_tests
step "C# core (Unity scripting target)" run_csharp_tests
step "Unity adapters (compile against shim)" run_unity_shim_check
step "C++ core (Unreal build settings)" run_cpp_tests

printf '\n\033[1m== summary ==\033[0m\n'
for line in "${results[@]}"; do
    printf '%s\n' "$line"
done

if [[ "$failures" -gt 0 ]]; then
    printf '\n%d step(s) failed\n' "$failures"
    exit 1
fi

printf '\nall verifiable steps passed\n'
