# Setup SSH Server on Windows for Splint Geo Processor
# Run this script as Administrator

#Requires -RunAsAdministrator

Write-Host "==================================="
Write-Host "Splint Geo Processor - SSH Setup"
Write-Host "==================================="
Write-Host ""

# Function to check if running as Administrator
function Test-Administrator {
    $currentUser = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($currentUser)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

if (-not (Test-Administrator)) {
    Write-Host "ERROR: This script must be run as Administrator" -ForegroundColor Red
    Write-Host "Right-click PowerShell and select 'Run as Administrator'" -ForegroundColor Yellow
    exit 1
}

Write-Host "Step 1: Checking OpenSSH Server installation..." -ForegroundColor Cyan
$sshServer = Get-WindowsCapability -Online | Where-Object Name -like 'OpenSSH.Server*'

if ($sshServer.State -eq "Installed") {
    Write-Host "✓ OpenSSH Server is already installed" -ForegroundColor Green
}
else {
    Write-Host "Installing OpenSSH Server..." -ForegroundColor Yellow
    Add-WindowsCapability -Online -Name OpenSSH.Server~~~~0.0.1.0
    Write-Host "✓ OpenSSH Server installed successfully" -ForegroundColor Green
}

Write-Host ""
Write-Host "Step 2: Configuring SSH service..." -ForegroundColor Cyan
Start-Service sshd -ErrorAction SilentlyContinue
Set-Service -Name sshd -StartupType 'Automatic'
Write-Host "✓ SSH service started and set to automatic startup" -ForegroundColor Green

Write-Host ""
Write-Host "Step 3: Configuring Windows Firewall..." -ForegroundColor Cyan
$firewallRule = Get-NetFirewallRule -Name "OpenSSH-Server-In-TCP" -ErrorAction SilentlyContinue

if ($firewallRule) {
    Write-Host "✓ Firewall rule already exists" -ForegroundColor Green
}
else {
    New-NetFirewallRule -Name 'OpenSSH-Server-In-TCP' -DisplayName 'OpenSSH Server (sshd)' -Enabled True -Direction Inbound -Protocol TCP -Action Allow -LocalPort 22
    Write-Host "✓ Firewall rule created for SSH (port 22)" -ForegroundColor Green
}

Write-Host ""
Write-Host "Step 4: Creating Splint Factory working directories..." -ForegroundColor Cyan
$baseDir = "$env:USERPROFILE\SplintFactoryFiles"
$dirs = @("inbox", "outbox", "logs")

foreach ($dir in $dirs) {
    $path = Join-Path $baseDir $dir
    if (-not (Test-Path $path)) {
        New-Item -Path $path -ItemType Directory -Force | Out-Null
        Write-Host "✓ Created: $path" -ForegroundColor Green
    }
    else {
        Write-Host "✓ Already exists: $path" -ForegroundColor Green
    }
}

Write-Host ""
Write-Host "Step 5: Configuring SSH server settings..." -ForegroundColor Cyan
$sshdConfigPath = "C:\ProgramData\ssh\sshd_config"
$sshdConfigBackup = "C:\ProgramData\ssh\sshd_config.backup"

# Backup existing config
if (Test-Path $sshdConfigPath) {
    Copy-Item $sshdConfigPath $sshdConfigBackup -Force
    Write-Host "✓ Backed up existing sshd_config" -ForegroundColor Green
}

# Create optimized sshd_config
$sshdConfig = @"
# OpenSSH Server Configuration for Splint Geo Processor

# Authentication
PubkeyAuthentication yes
PasswordAuthentication yes
PermitEmptyPasswords no

# Security
PermitRootLogin no
StrictModes yes

# Performance
UseDNS no

# Keep connections alive
ClientAliveInterval 60
ClientAliveCountMax 3

# Logging
SyslogFacility AUTH
LogLevel INFO

# Subsystems
Subsystem sftp sftp-server.exe
Subsystem powershell C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe -sshs -NoLogo -NoProfile
"@

Set-Content -Path $sshdConfigPath -Value $sshdConfig -Force
Write-Host "✓ SSH configuration updated" -ForegroundColor Green

Write-Host ""
Write-Host "Step 6: Creating .ssh directory for current user..." -ForegroundColor Cyan
$sshDir = "$env:USERPROFILE\.ssh"
if (-not (Test-Path $sshDir)) {
    New-Item -Path $sshDir -ItemType Directory -Force | Out-Null
    Write-Host "✓ Created: $sshDir" -ForegroundColor Green
}
else {
    Write-Host "✓ Already exists: $sshDir" -ForegroundColor Green
}

# Create empty authorized_keys file if it doesn't exist
$authorizedKeysPath = Join-Path $sshDir "authorized_keys"
if (-not (Test-Path $authorizedKeysPath)) {
    New-Item -Path $authorizedKeysPath -ItemType File -Force | Out-Null
    Write-Host "✓ Created: $authorizedKeysPath" -ForegroundColor Green
}
else {
    Write-Host "✓ Already exists: $authorizedKeysPath" -ForegroundColor Green
}

Write-Host ""
Write-Host "Step 7: Setting correct permissions on authorized_keys..." -ForegroundColor Cyan
# Set proper permissions on authorized_keys file
icacls $authorizedKeysPath /inheritance:r | Out-Null
icacls $authorizedKeysPath /grant:r "${env:USERNAME}:(R)" | Out-Null
icacls $authorizedKeysPath /grant:r "SYSTEM:(F)" | Out-Null
Write-Host "✓ Permissions set correctly" -ForegroundColor Green

Write-Host ""
Write-Host "Step 8: Restarting SSH service to apply changes..." -ForegroundColor Cyan
Restart-Service sshd
Write-Host "✓ SSH service restarted" -ForegroundColor Green

Write-Host ""
Write-Host "Step 9: Verifying SSH service status..." -ForegroundColor Cyan
$sshService = Get-Service sshd
if ($sshService.Status -eq "Running") {
    Write-Host "✓ SSH service is running" -ForegroundColor Green
}
else {
    Write-Host "✗ SSH service is not running" -ForegroundColor Red
    exit 1
}

Write-Host ""
Write-Host "========================================" -ForegroundColor Green
Write-Host "SSH Setup Complete!" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
Write-Host ""
Write-Host "Next Steps:" -ForegroundColor Yellow
Write-Host "1. Get your Windows machine's IP address:" -ForegroundColor White
Write-Host "   ipconfig" -ForegroundColor Gray
Write-Host ""
Write-Host "2. From your Mac, copy your SSH public key:" -ForegroundColor White
Write-Host "   cat ~/.ssh/id_ed25519.pub" -ForegroundColor Gray
Write-Host ""
Write-Host "3. Add the public key to this file:" -ForegroundColor White
Write-Host "   $authorizedKeysPath" -ForegroundColor Gray
Write-Host "   (Open with: notepad $authorizedKeysPath)" -ForegroundColor Gray
Write-Host ""
Write-Host "4. Test connection from Mac:" -ForegroundColor White
Write-Host "   ssh $env:USERNAME@<windows-ip>" -ForegroundColor Gray
Write-Host ""
Write-Host "5. Optional - Disable password authentication for security:" -ForegroundColor White
Write-Host "   After confirming key-based auth works, edit:" -ForegroundColor Gray
Write-Host "   $sshdConfigPath" -ForegroundColor Gray
Write-Host "   Change 'PasswordAuthentication yes' to 'PasswordAuthentication no'" -ForegroundColor Gray
Write-Host "   Then restart SSH: Restart-Service sshd" -ForegroundColor Gray
Write-Host ""

# Display network information
Write-Host "Current Network Information:" -ForegroundColor Yellow
Get-NetIPAddress -AddressFamily IPv4 | Where-Object {$_.InterfaceAlias -notlike "*Loopback*"} | Select-Object InterfaceAlias, IPAddress | Format-Table -AutoSize

Write-Host "Press any key to exit..."
$null = $Host.UI.RawUI.ReadKey("NoEcho,IncludeKeyDown")
