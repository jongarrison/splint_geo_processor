# Enable SSH Server Auto-Start on Windows
# Run this script as Administrator to ensure SSH server starts automatically on boot

#Requires -RunAsAdministrator

Write-Host "==================================="
Write-Host "Enable SSH Auto-Start on Windows"
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

Write-Host "Step 1: Checking if SSH Server is installed..." -ForegroundColor Cyan
$sshServer = Get-WindowsCapability -Online | Where-Object Name -like 'OpenSSH.Server*'

if ($sshServer.State -eq "Installed") {
    Write-Host "✓ OpenSSH Server is installed" -ForegroundColor Green
}
else {
    Write-Host "✗ OpenSSH Server is NOT installed" -ForegroundColor Red
    Write-Host "  Run setup-windows-ssh.ps1 first to install SSH server" -ForegroundColor Yellow
    exit 1
}

Write-Host ""
Write-Host "Step 2: Checking current SSH service configuration..." -ForegroundColor Cyan
$sshService = Get-Service -Name sshd -ErrorAction SilentlyContinue

if (-not $sshService) {
    Write-Host "✗ SSH service (sshd) not found" -ForegroundColor Red
    exit 1
}

Write-Host "  Current Status: $($sshService.Status)" -ForegroundColor White
Write-Host "  Current Startup Type: $($sshService.StartType)" -ForegroundColor White

Write-Host ""
Write-Host "Step 3: Configuring SSH service to start automatically..." -ForegroundColor Cyan

# Set the service to start automatically
Set-Service -Name sshd -StartupType 'Automatic'
Write-Host "✓ SSH service set to Automatic startup" -ForegroundColor Green

# Also ensure the ssh-agent is set to automatic (helpful for key management)
Set-Service -Name ssh-agent -StartupType 'Automatic' -ErrorAction SilentlyContinue
Write-Host "✓ SSH agent set to Automatic startup" -ForegroundColor Green

Write-Host ""
Write-Host "Step 4: Starting SSH service if not running..." -ForegroundColor Cyan

$sshService = Get-Service -Name sshd
if ($sshService.Status -ne "Running") {
    Start-Service sshd
    Write-Host "✓ SSH service started" -ForegroundColor Green
}
else {
    Write-Host "✓ SSH service is already running" -ForegroundColor Green
}

Write-Host ""
Write-Host "Step 5: Verifying configuration..." -ForegroundColor Cyan
$sshService = Get-Service -Name sshd

Write-Host ""
Write-Host "Current SSH Service Status:" -ForegroundColor Yellow
Write-Host "  Status: $($sshService.Status)" -ForegroundColor $(if ($sshService.Status -eq "Running") { "Green" } else { "Red" })
Write-Host "  Startup Type: $($sshService.StartType)" -ForegroundColor $(if ($sshService.StartType -eq "Automatic") { "Green" } else { "Red" })

if ($sshService.Status -eq "Running" -and $sshService.StartType -eq "Automatic") {
    Write-Host ""
    Write-Host "========================================" -ForegroundColor Green
    Write-Host "SUCCESS! SSH Server Auto-Start Enabled" -ForegroundColor Green
    Write-Host "========================================" -ForegroundColor Green
    Write-Host ""
    Write-Host "The SSH server will now start automatically when Windows boots." -ForegroundColor White
    Write-Host ""
    Write-Host "You can verify this after reboot by running:" -ForegroundColor Yellow
    Write-Host "  Get-Service sshd" -ForegroundColor Gray
    Write-Host ""
    
    # Check for Bonjour/mDNS service
    Write-Host "Step 6: Checking Bonjour/mDNS configuration..." -ForegroundColor Cyan
    $bonjourService = Get-Service -Name "Bonjour Service" -ErrorAction SilentlyContinue
    
    if ($bonjourService) {
        Write-Host "✓ Bonjour service is installed" -ForegroundColor Green
        
        # Set Bonjour to start automatically
        if ($bonjourService.StartType -ne "Automatic") {
            Set-Service -Name "Bonjour Service" -StartupType 'Automatic'
            Write-Host "✓ Bonjour service set to Automatic startup" -ForegroundColor Green
        }
        else {
            Write-Host "✓ Bonjour service already set to Automatic startup" -ForegroundColor Green
        }
        
        # Start it if not running
        if ($bonjourService.Status -ne "Running") {
            Write-Host "  Starting Bonjour service..." -ForegroundColor Yellow
            Start-Service "Bonjour Service" -ErrorAction SilentlyContinue
            Start-Sleep -Seconds 1
            $bonjourService = Get-Service -Name "Bonjour Service"
            if ($bonjourService.Status -eq "Running") {
                Write-Host "✓ Bonjour service started" -ForegroundColor Green
            }
            else {
                Write-Host "! Could not start Bonjour service" -ForegroundColor Yellow
            }
        }
        else {
            Write-Host "✓ Bonjour service is running" -ForegroundColor Green
        }
        
        Write-Host "  This machine should be reachable via <hostname>.local" -ForegroundColor White
    }
    else {
        Write-Host "! Bonjour/mDNS service not installed" -ForegroundColor Yellow
        Write-Host ""
        Write-Host "  Without Bonjour, this machine cannot be reached via .local hostname" -ForegroundColor Yellow
        Write-Host "  (e.g., lazyboy2000.local won't work)" -ForegroundColor Yellow
        Write-Host ""
        Write-Host "  Options:" -ForegroundColor White
        Write-Host "  1. Install Bonjour Print Services from Apple:" -ForegroundColor Gray
        Write-Host "     https://support.apple.com/kb/DL999" -ForegroundColor Gray
        Write-Host "  2. Use the IP address directly instead of .local hostname" -ForegroundColor Gray
        Write-Host ""
    }
    
    Write-Host ""
    Write-Host "Current Network Information:" -ForegroundColor Yellow
    Get-NetIPAddress -AddressFamily IPv4 | Where-Object {$_.InterfaceAlias -notlike "*Loopback*"} | Select-Object InterfaceAlias, IPAddress | Format-Table -AutoSize
    Write-Host ""
    Write-Host "To connect from Mac, use:" -ForegroundColor White
    $hostname = $env:COMPUTERNAME.ToLower()
    if ($bonjourService -and $bonjourService.Status -eq "Running") {
        Write-Host "  ssh $env:USERNAME@$hostname.local" -ForegroundColor Gray
    }
    Write-Host "  ssh $env:USERNAME@<IP-ADDRESS-FROM-ABOVE>" -ForegroundColor Gray
}
else {
    Write-Host ""
    Write-Host "✗ Configuration incomplete. Please check the errors above." -ForegroundColor Red
    exit 1
}

Write-Host ""
Write-Host "Press any key to exit..."
$null = $Host.UI.RawUI.ReadKey("NoEcho,IncludeKeyDown")
