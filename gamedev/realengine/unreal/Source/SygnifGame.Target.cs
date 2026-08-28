using UnrealBuildTool;

public class SygnifGameTarget : TargetRules
{
	public SygnifGameTarget(TargetInfo Target) : base(Target)
	{
		Type = TargetType.Game;

		// Unreal 5.4's current defaults. Pinning the version explicitly rather
		// than tracking Latest means an engine upgrade cannot silently change
		// build semantics underneath the project.
		DefaultBuildSettings = BuildSettingsVersion.V4;
		IncludeOrderVersion = EngineIncludeOrderVersion.Unreal5_4;

		ExtraModuleNames.Add("SygnifGame");
	}
}
