# Rhino WIP .NET Version Mismatch Fix

## Issue

**Bug in RhinoWIP (Nov 20, 2024 update and later):**

`RhinoCode.runtimeconfig.json` has mismatched versions - tfm is net9.0 but requests version 8.0.0, while RhinoWIP only ships .NET 9.0.1.

### Symptoms

When running `rhinocode list --json`, you see:

```
Command failed: /Applications/RhinoWIP.app/Contents/Resources/bin/rhinocode list --json
You must install or update .NET to run this application.

App: /Applications/RhinoWIP.app/Contents/Frameworks/RhCore.framework/Versions/A/Resources/RhinoCode.dll
Architecture: arm64
Framework: 'Microsoft.NETCore.App', version '8.0.0' (arm64)
.NET location: /Applications/RhinoWIP.app/Contents/Frameworks/RhCore.framework/Versions/A/Resources/dotnet/arm64/

The following frameworks were found:
  9.0.1 at [/Applications/RhinoWIP.app/Contents/Frameworks/RhCore.framework/Versions/A/Resources/dotnet/arm64/shared/Microsoft.NETCore.App]
```

## Fix (macOS)

Run this command to patch the config file:

```bash
sed -i '' 's/"version": "8.0.0"/"version": "9.0.0"/' /Applications/RhinoWIP.app/Contents/Frameworks/RhCore.framework/Versions/A/Resources/RhinoCode.runtimeconfig.json
```

### Note

You may need to **reapply this fix after RhinoWIP updates** until fixed upstream by McNeel.

## Verification

After applying the fix, verify RhinoCode can connect:

```bash
/Applications/RhinoWIP.app/Contents/Resources/bin/rhinocode list --json
```

Should return an empty array `[]` or list of running Rhino instances if Rhino is already open.

## Windows

This issue appears to be macOS-specific. Windows RhinoWIP installations have not shown this problem.
