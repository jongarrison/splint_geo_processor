# Bootstrap script for a fresh Azure Windows Server VM running splint_geo_processor.
# Run in elevated PowerShell on the VM, AFTER:
#   - Rhino 8 installed and signed in
#   - Bambu Studio installed
#   - Grasshopper opened once to install plugins
#
# This script:
#   - Installs Chocolatey, Node.js LTS, Git
#   - Installs/configures OpenSSH Server with Git Bash as default shell
#     (matches the lazyboy2000.local SSH setup so deploy scripts work identically)
#   - Clones the splint_geo_processor repo
#   - Sets ENV_MODE=production as a persistent user environment variable
#   - Prepares ~/.ssh/authorized_keys (you paste your Mac key into it after)
#
# After this completes, register the scheduled task with:
#   .\scripts\setup-windows-startup.ps1

#Requires -RunAsAdministrator

$ErrorActionPreference = "Stop"

Write-Host "splint_geo_processor - Azure VM Bootstrap" -ForegroundColor Cyan
Write-Host "==========================================" -ForegroundColor Cyan
Write-Host ""

# ---------- 1. Chocolatey ----------
if (-not (Get-Command choco -ErrorAction SilentlyContinue)) {
    Write-Host "Installing Chocolatey..." -ForegroundColor Yellow
    Set-ExecutionPolicy Bypass -Scope Process -Force
    [System.Net.ServicePointManager]::SecurityProtocol = [System.Net.ServicePointManager]::SecurityProtocol -bor 3072
    iex ((New-Object System.Net.WebClient).DownloadString('https://community.chocolatey.org/install.ps1'))
    # Refresh PATH so choco is callable in this session
    $env:Path = [System.Environment]::GetEnvironmentVariable("Path","Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path","User")
} else {
    Write-Host "Chocolatey already installed." -ForegroundColor Green
}

# ---------- 2. Node.js LTS + Git ----------
Write-Host "Installing Node.js LTS and Git..." -ForegroundColor Yellow
choco install nodejs-lts git -y --no-progress
# Refresh PATH
$env:Path = [System.Environment]::GetEnvironmentVariable("Path","Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path","User")

# ---------- 3. OpenSSH Server ----------
Write-Host "Installing OpenSSH Server..." -ForegroundColor Yellow
$sshFeature = Get-WindowsCapability -Online | Where-Object Name -like 'OpenSSH.Server*'
if ($sshFeature.State -ne 'Installed') {
    Add-WindowsCapability -Online -Name $sshFeature.Name | Out-Null
}
Start-Service sshd
Set-Service -Name sshd -StartupType Automatic

# Open firewall for SSH (idempotent)
$rule = Get-NetFirewallRule -Name "OpenSSH-Server-In-TCP" -ErrorAction SilentlyContinue
if (-not $rule) {
    New-NetFirewallRule -Name "OpenSSH-Server-In-TCP" -DisplayName "OpenSSH Server (sshd)" `
        -Enabled True -Direction Inbound -Protocol TCP -Action Allow -LocalPort 22 | Out-Null
}

# Set Git Bash as default shell for SSH (matches lazyboy2000.local setup)
$gitBash = "C:\Program Files\Git\bin\bash.exe"
if (Test-Path $gitBash) {
    New-ItemProperty -Path "HKLM:\SOFTWARE\OpenSSH" -Name DefaultShell `
        -Value $gitBash -PropertyType String -Force | Out-Null
    Write-Host "  SSH default shell set to Git Bash" -ForegroundColor Green
} else {
    Write-Host "  WARNING: Git Bash not found; SSH default shell unchanged" -ForegroundColor Yellow
}

# ---------- 4. SSH key setup ----------
$adminUser = $env:USERNAME
$sshDir = "C:\Users\$adminUser\.ssh"
if (-not (Test-Path $sshDir)) {
    New-Item -ItemType Directory -Path $sshDir | Out-Null
}
$authKeys = Join-Path $sshDir "authorized_keys"
if (-not (Test-Path $authKeys)) {
    New-Item -ItemType File -Path $authKeys | Out-Null
}
# Lock down ACLs on authorized_keys (sshd refuses keys with loose permissions)
icacls $authKeys /inheritance:r /grant "${adminUser}:F" "SYSTEM:F" | Out-Null

# ---------- 5. Persistent ENV_MODE ----------
[System.Environment]::SetEnvironmentVariable('ENV_MODE', 'production', 'User')
Write-Host "Set ENV_MODE=production as persistent user environment variable" -ForegroundColor Green

# ---------- 6. Clone repo ----------
$workDir = "C:\Users\$adminUser\work"
if (-not (Test-Path $workDir)) {
    New-Item -ItemType Directory -Path $workDir | Out-Null
}
$repoDir = Join-Path $workDir "splint_geo_processor"
if (-not (Test-Path $repoDir)) {
    Write-Host "Cloning splint_geo_processor..." -ForegroundColor Yellow
    Push-Location $workDir
    git clone https://github.com/jongarrison/splint_geo_processor.git
    Pop-Location
} else {
    Write-Host "Repo already cloned at $repoDir" -ForegroundColor Green
}

# ---------- 7. Done ----------
Write-Host ""
Write-Host "================================================================" -ForegroundColor Green
Write-Host "Bootstrap complete." -ForegroundColor Green
Write-Host "================================================================" -ForegroundColor Green
Write-Host ""
Write-Host "Next steps:" -ForegroundColor Cyan
Write-Host ""
Write-Host "  1. Paste your Mac's SSH public key into:"
Write-Host "       $authKeys"
Write-Host "     (Get the key on Mac with: cat ~/.ssh/id_ed25519.pub)"
Write-Host ""
Write-Host "  2. Create the .env secrets file at:"
Write-Host "       $repoDir\.env"
Write-Host "     Containing at minimum: SF_API_KEY=<your-key>"
Write-Host ""
Write-Host "  3. Verify .env.platform.win has correct Rhino/Bambu paths."
Write-Host ""
Write-Host "  4. Open Grasshopper once if you haven't already (lets plugins install)."
Write-Host ""
Write-Host "  5. Register the scheduled task:"
Write-Host "       cd $repoDir"
Write-Host "       .\scripts\setup-windows-startup.ps1"
Write-Host ""
Write-Host "  6. From Mac, test SSH:"
Write-Host "       ssh ${adminUser}@<your-vm>.eastus.cloudapp.azure.com"
Write-Host ""
