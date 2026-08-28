#!/usr/bin/env bash
# Copy the canonical GameCore sources into the Unity project.
#
# There is exactly one copy of the gameplay core, at realengine/csharp/GameCore.
# Unity cannot reference C# files outside its Assets folder, so the sources have
# to get in there somehow. The three options and why this one:
#
#   1. Build GameCore.dll and drop it in Assets/Plugins. Works, but the core
#      stops being debuggable and steppable from the editor, and every change
#      needs a rebuild-and-copy before Unity sees it.
#   2. Develop the core inside Assets and have the standalone csproj link the
#      files back out. Also works, and is what a studio with one game usually
#      does. Rejected here because the core is shared with the C++/Unreal side
#      and should not live inside one engine's project.
#   3. Keep the core outside and sync a copy in. Chosen: the editor sees plain
#      C# it can step through, and the canonical location stays engine-neutral.
#
# The copy is gitignored, so it can never be edited by mistake and committed —
# edits go to the canonical location and get synced.
#
# The assembly definition beside the synced files sets "noEngineReferences":
# true, which makes Unity's own compiler enforce the architecture. Any attempt
# to add "using UnityEngine;" to a core file becomes a compile error in the
# editor rather than a slow erosion nobody notices until the tests need a
# running engine.

set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source_dir="$script_dir/../csharp/GameCore"
target_dir="$script_dir/Assets/Scripts/Core"

if [[ ! -d "$source_dir" ]]; then
    echo "error: canonical core not found at $source_dir" >&2
    exit 1
fi

mkdir -p "$target_dir"

# Remove previously synced files so a deletion upstream propagates. The asmdef
# and .gitignore are the only tracked files here and must survive.
find "$target_dir" -name '*.cs' -delete

count=0
while IFS= read -r -d '' file; do
    relative="${file#"$source_dir"/}"
    destination="$target_dir/$relative"
    mkdir -p "$(dirname "$destination")"
    cp "$file" "$destination"
    count=$((count + 1))
done < <(find "$source_dir" -name '*.cs' -not -path '*/obj/*' -not -path '*/bin/*' -print0)

echo "synced $count core source files into Assets/Scripts/Core"
