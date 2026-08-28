using UnrealBuildTool;

public class SygnifGameEditorTarget : TargetRules
{
	public SygnifGameEditorTarget(TargetInfo Target) : base(Target)
	{
		Type = TargetType.Editor;
		DefaultBuildSettings = BuildSettingsVersion.V4;
		IncludeOrderVersion = EngineIncludeOrderVersion.Unreal5_4;

		ExtraModuleNames.Add("SygnifGame");
	}
}
