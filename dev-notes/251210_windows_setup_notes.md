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

### Install as Windows Service
1. Run as Administrator:
   ```powershell
   .\scripts\setup-windows-service.ps1
   ```
2. Script will:
   - Install NSSM (service manager) via Chocolatey
   - Build TypeScript project
   - Create Windows service "SplintGeoProcessor"
   - Configure auto-start and crash recovery
   - Start the service

### Service Management
```powershell
# Check status
Get-Service SplintGeoProcessor

# Restart
Restart-Service SplintGeoProcessor

# View logs
Get-Content ~\SplintFactoryFiles\logs\service-stdout.log -Tail 50
```

## Installed Paths
- Rhino 9 WIP: `C:\Program Files\Rhino 9 WIP\System\`
- BambuStudio: `C:\Program Files\Bambu Studio\`
- Node.js: Check with `where.exe node`
- Git Bash: `C:\Program Files\Git\bin\bash.exe`
