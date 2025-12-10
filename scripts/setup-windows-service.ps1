# Setup Splint Geo Processor as Windows Service
# Run this script as Administrator

#Requires -RunAsAdministrator

Write-Host "=======================================" -ForegroundColor Cyan
Write-Host "Splint Geo Processor - Service Setup" -ForegroundColor Cyan
Write-Host "=======================================" -ForegroundColor Cyan
Write-Host ""

# Install NSSM (Non-Sucking Service Manager) if not present
Write-Host "Step 1: Installing NSSM (service manager)..." -ForegroundColor Cyan
$nssmPath = "C:\ProgramData\chocolatey\bin\nssm.exe"
if (-not (Test-Path $nssmPath)) {
    choco install nssm -y
    Write-Host "✓ NSSM installed" -ForegroundColor Green
} else {
    Write-Host "✓ NSSM already installed" -ForegroundColor Green
}

# Get the current directory (where splint_geo_processor is located)
$repoPath = Read-Host "Enter the full path to splint_geo_processor directory (e.g., C:\Users\lazyb\work\splint_geo_processor)"
if (-not (Test-Path $repoPath)) {
    Write-Host "✗ Directory not found: $repoPath" -ForegroundColor Red
    exit 1
}

# Check if node is installed
Write-Host ""
Write-Host "Step 2: Checking Node.js installation..." -ForegroundColor Cyan
$nodePath = (Get-Command node -ErrorAction SilentlyContinue).Source
if (-not $nodePath) {
    Write-Host "✗ Node.js not found. Please install Node.js first." -ForegroundColor Red
    exit 1
}
Write-Host "✓ Node.js found at: $nodePath" -ForegroundColor Green

# Check if config.json exists
Write-Host ""
Write-Host "Step 3: Checking configuration..." -ForegroundColor Cyan
$configPath = Join-Path $repoPath "secrets\config.json"
if (-not (Test-Path $configPath)) {
    Write-Host "✗ Configuration file not found: $configPath" -ForegroundColor Red
    Write-Host ""
    Write-Host "Please create secrets/config.json based on secrets/config.json.example" -ForegroundColor Yellow
    Write-Host "You need to:" -ForegroundColor Yellow
    Write-Host "1. Copy secrets/config.json.example to secrets/config.json" -ForegroundColor White
    Write-Host "2. Set your API key from splintfactory.com" -ForegroundColor White
    Write-Host "3. Verify the paths to Rhino and BambuStudio" -ForegroundColor White
    exit 1
}
Write-Host "✓ Configuration file found" -ForegroundColor Green

# Build the TypeScript project
Write-Host ""
Write-Host "Step 4: Building TypeScript project..." -ForegroundColor Cyan
Push-Location $repoPath
try {
    npm run build
    if ($LASTEXITCODE -ne 0) {
        throw "Build failed"
    }
    Write-Host "✓ Project built successfully" -ForegroundColor Green
} catch {
    Write-Host "✗ Build failed. Please fix build errors first." -ForegroundColor Red
    Pop-Location
    exit 1
}
Pop-Location

# Install/update the service
Write-Host ""
Write-Host "Step 5: Installing Windows service..." -ForegroundColor Cyan
$serviceName = "SplintGeoProcessor"
$existingService = Get-Service -Name $serviceName -ErrorAction SilentlyContinue

if ($existingService) {
    Write-Host "Service already exists. Stopping and removing..." -ForegroundColor Yellow
    Stop-Service -Name $serviceName -Force -ErrorAction SilentlyContinue
    & $nssmPath remove $serviceName confirm
}

# Create the service
$nodeExe = $nodePath
$appPath = Join-Path $repoPath "dist\index.js"
& $nssmPath install $serviceName $nodeExe $appPath

# Configure service
& $nssmPath set $serviceName AppDirectory $repoPath
& $nssmPath set $serviceName DisplayName "Splint Geo Processor"
& $nssmPath set $serviceName Description "Processes 3D geometry jobs for Splint Factory"
& $nssmPath set $serviceName Start SERVICE_AUTO_START
& $nssmPath set $serviceName AppRestartDelay 5000
& $nssmPath set $serviceName AppStdout (Join-Path $env:USERPROFILE "SplintFactoryFiles\logs\service-stdout.log")
& $nssmPath set $serviceName AppStderr (Join-Path $env:USERPROFILE "SplintFactoryFiles\logs\service-stderr.log")
& $nssmPath set $serviceName AppRotateFiles 1
& $nssmPath set $serviceName AppRotateBytes 10485760  # 10MB

Write-Host "✓ Service installed" -ForegroundColor Green

# Start the service
Write-Host ""
Write-Host "Step 6: Starting service..." -ForegroundColor Cyan
Start-Service -Name $serviceName
Start-Sleep -Seconds 2

$service = Get-Service -Name $serviceName
if ($service.Status -eq "Running") {
    Write-Host "✓ Service is running" -ForegroundColor Green
} else {
    Write-Host "✗ Service failed to start. Status: $($service.Status)" -ForegroundColor Red
    Write-Host "Check logs at: $env:USERPROFILE\SplintFactoryFiles\logs\" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "=======================================" -ForegroundColor Green
Write-Host "Setup Complete!" -ForegroundColor Green
Write-Host "=======================================" -ForegroundColor Green
Write-Host ""
Write-Host "Service Name: $serviceName" -ForegroundColor White
Write-Host "Status: " -NoNewline -ForegroundColor White
Write-Host (Get-Service -Name $serviceName).Status -ForegroundColor $(if ((Get-Service -Name $serviceName).Status -eq 'Running') { 'Green' } else { 'Red' })
Write-Host ""
Write-Host "Useful Commands:" -ForegroundColor Yellow
Write-Host "  Get-Service $serviceName           # Check status" -ForegroundColor Gray
Write-Host "  Restart-Service $serviceName       # Restart service" -ForegroundColor Gray
Write-Host "  Stop-Service $serviceName          # Stop service" -ForegroundColor Gray
Write-Host "  Get-Content ~\SplintFactoryFiles\logs\service-stdout.log -Tail 50  # View logs" -ForegroundColor Gray
Write-Host ""
