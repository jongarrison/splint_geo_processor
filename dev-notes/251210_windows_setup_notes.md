# Windows Setup Notes - Dec 10, 2024

## Prerequisites (Already Installed)
- Git Bash (C:\Program Files\Git\bin\bash.exe)
- Chocolatey package manager
- Rhino3D
- BambuStudio

## SSH Setup
1. Ran `scripts/setup-windows-ssh.ps1` as Administrator
   - Installed OpenSSH Server
   - Configured auto-start for sshd service
   - Created SplintFactoryFiles directories (inbox/outbox/logs)
   - Configured firewall rule for port 22
2. Configured Git Bash as default SSH shell:
   ```powershell
   New-ItemProperty -Path "HKLM:\SOFTWARE\OpenSSH" -Name DefaultShell -Value "C:\Program Files\Git\bin\bash.exe" -PropertyType String -Force
   Restart-Service sshd
   ```

## Bonjour/.local Network Name
1. Installed Bonjour via Chocolatey:
   ```powershell
   choco install bonjour -y
   ```
2. Machine now accessible at: `lazyboy2000.local`

## Windows Auto-Login Configuration
For unattended operation (e.g., after power outages), configure Windows to automatically login:

**Note:** Windows 11 Home doesn't show the netplwiz checkbox for auto-login. Use registry method instead.

1. Via SSH (using Git Bash), run these commands with appropriate username/password:
   ```bash
   ssh <hostname>.local 'cmd.exe /c "reg add \"HKLM\\SOFTWARE\\Microsoft\\Windows NT\\CurrentVersion\\Winlogon\" /v AutoAdminLogon /t REG_SZ /d 1 /f"'
   ssh <hostname>.local 'cmd.exe /c "reg add \"HKLM\\SOFTWARE\\Microsoft\\Windows NT\\CurrentVersion\\Winlogon\" /v DefaultUserName /t REG_SZ /d <username> /f"'
   ssh <hostname>.local 'cmd.exe /c "reg add \"HKLM\\SOFTWARE\\Microsoft\\Windows NT\\CurrentVersion\\Winlogon\" /v DefaultPassword /t REG_SZ /d <password> /f"'
   ```

2. Test by restarting the machine:
   ```bash
   ssh <hostname>.local 'cmd.exe /c "shutdown /r /t 5 /f"'
   ```

**Security Note:** This stores the password in plaintext in the registry. Only use on machines in secure physical locations.

## Splint Geo Processor Setup

### Configuration
1. Clone repo to Windows machine:
   ```bash
   git clone https://github.com/jongarrison/splint_geo_processor.git
   cd splint_geo_processor
   npm install
   ```

2. Create configuration file:
   ```bash
   cp secrets/config.json.example secrets/config.json
   ```

3. Edit `secrets/config.json`:
   - Set `SF_API_KEY` (generate from splintfactory.com → API Keys)
   - Verify paths:
     - `RHINO_CLI`: `C:\Program Files\Rhino 9 WIP\System\Rhino.exe`
     - `RHINOCODE_CLI`: `C:\Program Files\Rhino 9 WIP\System\RhinoCode.exe`
     - `BAMBU_CLI`: `C:\Program Files\Bambu Studio\bambu-studio.exe`

### Configure Rhino for RhinoCode (One-Time Setup)

**CRITICAL:** Rhino must be configured to start the script server for RhinoCode CLI to work.

1. Launch Rhino manually
2. Go to **Tools** → **Options** → **General**
3. In the **Command lists to run** section under **When Rhino starts**, add:
   ```
   StartScriptServer
   ```
4. Click **OK** and close Rhino

**Test RhinoCode connectivity:**
```powershell
# Launch Rhino (it will auto-start the script server)
Start-Process "C:\Program Files\Rhino 9 WIP\System\Rhino.exe"

# Wait a few seconds, then verify RhinoCode can see it
& "C:\Program Files\Rhino 9 WIP\System\RhinoCode.exe" list --json
# Should return a non-empty array with Rhino instance info
```

### Configure Grasshopper Plugins (One-Time Setup)

1. Launch Rhino
2. Type `Grasshopper` command to open Grasshopper
3. Open a test splint .gh file (e.g., from splint_generators_gh repository)
4. Let Grasshopper install any required plugins
5. Verify the script runs without errors
6. Close Grasshopper and Rhino

### Install as Startup Task

**Note:** Windows services cannot launch GUI applications. We use a scheduled task instead,
which runs in the user's interactive session.

1. Run as Administrator:
   ```powershell
   .\scripts\setup-windows-startup.ps1
   ```
2. Script will:
   - Build TypeScript project
   - Create scheduled task "SplintGeoProcessor" to run at user logon
   - Configure task to run in interactive session (allows launching Rhino GUI)
   - Start the task immediately

### Task Management
```powershell
# Check status
Get-ScheduledTask SplintGeoProcessor
Get-ScheduledTaskInfo SplintGeoProcessor

# Start/stop manually
Start-ScheduledTask SplintGeoProcessor
Stop-ScheduledTask SplintGeoProcessor

# View logs
Get-Content ~\SplintFactoryFiles\logs\geo-processor.log -Tail 50
```

## Installed Paths
- Rhino 9 WIP: `C:\Program Files\Rhino 9 WIP\System\`
- BambuStudio: `C:\Program Files\Bambu Studio\`
- Node.js: Check with `where.exe node`
- Git Bash: `C:\Program Files\Git\bin\bash.exe`
