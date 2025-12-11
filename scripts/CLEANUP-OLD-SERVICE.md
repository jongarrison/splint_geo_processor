# Cleanup Old Windows Service

If you previously installed SplintGeoProcessor as a Windows service, remove it before setting up the scheduled task:

```powershell
# Stop and remove the service
Stop-Service SplintGeoProcessor -ErrorAction SilentlyContinue
sc.exe delete SplintGeoProcessor

# Verify it's gone
Get-Service SplintGeoProcessor -ErrorAction SilentlyContinue
```

Then proceed with the new scheduled task setup:
```powershell
.\scripts\setup-windows-startup.ps1
```
