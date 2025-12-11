# Windows Service Approach - DEPRECATED

This file documents why the Windows service approach was abandoned.

## Problem

Windows services run in **Session 0** (non-interactive), while user desktops run in **Session 1** (interactive). This is a security isolation feature introduced in Windows Vista.

Even when a service is configured to run as a user account, it **cannot launch GUI applications** into the user's desktop session.

## What We Tried

1. **Running service as user account** (not LocalSystem)
   - Result: Service could run Node.js but couldn't launch Rhino GUI

2. **Using `cmd.exe /c start` to launch Rhino**
   - Result: Command succeeded but Rhino didn't appear in user session

3. **Using PowerShell `Start-Process` to launch Rhino**
   - Result: Command succeeded quickly but Rhino didn't launch

4. **Configuring service as interactive (SERVICE_INTERACTIVE_PROCESS)**
   - Result: Only works with LocalSystem, and even then is deprecated and doesn't work in modern Windows

## Solution

Use **Windows Scheduled Tasks** instead of services. Scheduled tasks can:
- Run at user logon
- Execute in the user's interactive session (Session 1)
- Launch GUI applications successfully
- Still provide auto-start and crash recovery

See `setup-windows-startup.ps1` for the working implementation.

## Files Deprecated

- `setup-windows-service.ps1` → Use `setup-windows-startup.ps1` instead
- All NSSM-based service configuration
