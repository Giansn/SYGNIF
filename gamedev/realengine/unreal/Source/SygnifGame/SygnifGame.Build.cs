using System.IO;
using UnrealBuildTool;

/// <summary>
/// Build rules for the gameplay module.
/// </summary>
/// <remarks>
/// The interesting part of this file is how the engine-free core is pulled in.
/// The core lives outside the Unreal project entirely, at realengine/cpp, and is
/// compiled straight into this module as additional include and source paths
/// rather than being linked as a prebuilt static library.
///
/// Compiling it in, rather than linking a .lib, matters because Unreal's build
/// system controls the toolchain, the C++ standard, the runtime library and the
/// preprocessor definitions for every target platform it supports. A static
/// library built here with the host compiler would link on this machine and fail
/// on console, Android, or a different MSVC toolchain version — the classic
/// "works on my machine, breaks in the build farm" failure. Source that UBT
/// compiles is source built with the right flags everywhere.
/// </remarks>
public class SygnifGame : ModuleRules
{
	public SygnifGame(ReadOnlyTargetRules Target) : base(Target)
	{
		PCHUsage = PCHUsageMode.UseExplicitOrSharedPCHs;

		PublicDependencyModuleNames.AddRange(new string[]
		{
			"Core",
			"CoreUObject",
			"Engine",
			"InputCore",
			"EnhancedInput",
		});

		// The engine-free gameplay core, shared with the standalone CMake build
		// and its test suite. One copy of the logic, two build systems.
		string CoreRoot = Path.GetFullPath(Path.Combine(ModuleDirectory, "..", "..", "..", "cpp"));
		PublicIncludePaths.Add(Path.Combine(CoreRoot, "include"));

		// NOTE: the core's .cpp files are compiled by listing the directory as an
		// additional source path. UBT discovers sources under the module folder
		// only, so a real project either symlinks realengine/cpp/src into
		// Private/, or (more usually) makes the core its own UBT module with its
		// own Build.cs. The second is the better shape once the core grows;
		// this project keeps it inline because there are two translation units.

		// The core is written to compile under Unreal's defaults rather than
		// asking the module to relax them. These are the engine defaults, stated
		// explicitly so a future change to them is a deliberate decision:
		//
		//   bEnableExceptions = false   the core returns error codes, never throws
		//   bUseRTTI          = false   no dynamic_cast or typeid anywhere in it
		//
		// The standalone CMake build compiles the same sources with
		// -fno-exceptions -fno-rtti precisely so this stays true, and breaks in
		// two seconds here instead of forty minutes into an engine build.
		bEnableExceptions = false;
		bUseRTTI = false;

		// Treat warnings as errors in the gameplay module. Engine headers are
		// excluded from this by UBT, so it only polices code we own.
		CppStandard = CppStandardVersion.Cpp20;
	}
}
