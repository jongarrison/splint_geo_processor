11/24/25 - RhinoWIP rhinocode broken after update

Bug in RhinoWIP Nov 20 update: `RhinoCode.runtimeconfig.json` has mismatched versions - tfm is net9.0 but requests version 8.0.0, while RhinoWIP only ships .NET 9.0.1.

Fix:
```bash
sed -i '' 's/"version": "8.0.0"/"version": "9.0.0"/' /Applications/RhinoWIP.app/Contents/Frameworks/RhCore.framework/Versions/A/Resources/RhinoCode.runtimeconfig.json
```

May need to reapply after RhinoWIP updates until fixed upstream.
