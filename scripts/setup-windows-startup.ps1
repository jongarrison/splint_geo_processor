# Setup Splint Geo Processor as Startup Task
# This runs the app in the user's interactive session, allowing GUI apps like Rhino to launch

Write-Host "Splint Geo Processor - Startup Task Setup" -ForegroundColor Cyan
Write-Host "==========================================" -ForegroundColor Cyan
Write-Host ""

# Auto-detect repository path from script location
$scriptPath = $PSScriptRoot
$repoPath = Split-Path -Parent $scriptPath
Write-Host "Repository path: $repoPath" -ForegroundColor Yellow
Write-Host ""

# Build the project
Write-Host "Step 1: Building TypeScript project..." -ForegroundColor Cyan
Push-Location $repoPath
npm install
npm run build
if ($LASTEXITCODE -ne 0) {
    Write-Host "✗ Build failed!" -ForegroundColor Red
    Pop-Location
    exit 1
}
Write-Host "✓ Build completed" -ForegroundColor Green
Pop-Location
Write-Host ""

# Get current user
$currentUser = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
Write-Host "Current user: $currentUser" -ForegroundColor Yellow
Write-Host ""

# Create scheduled task
Write-Host "Step 2: Creating scheduled task..." -ForegroundColor Cyan
$taskName = "SplintGeoProcessor"

# Remove existing task if it exists
$existingTask = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($existingTask) {
    Write-Host "Task already exists. Removing..." -ForegroundColor Yellow
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
}

# Create action - run node with the built JavaScript
$nodePath = (Get-Command node).Source
$scriptPath = Join-Path $repoPath "dist\index.js"
$action = New-ScheduledTaskAction -Execute $nodePath -Argument $scriptPath -WorkingDirectory $repoPath

# Create trigger - at user logon
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $currentUser

# Create settings
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -RunOnlyIfNetworkAvailable `
    -DontStopOnIdleEnd `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1)

# Create principal - run as current user with highest privileges
$principal = New-ScheduledTaskPrincipal -UserId $currentUser -LogonType Interactive -RunLevel Highest

# Register the task
Register-ScheduledTask `
    -TaskName $taskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Principal $principal `
    -Description "Splint Geo Processor - Processes 3D geometry jobs for SplintFactory" | Out-Null

Write-Host "✓ Scheduled task created: $taskName" -ForegroundColor Green
Write-Host ""

# Start the task immediately
Write-Host "Step 3: Starting task..." -ForegroundColor Cyan
Start-ScheduledTask -TaskName $taskName
Start-Sleep -Seconds 2

# Check if it's running
$task = Get-ScheduledTask -TaskName $taskName
$taskInfo = Get-ScheduledTaskInfo -TaskName $taskName
Write-Host "✓ Task started" -ForegroundColor Green
Write-Host "  Last Run: $($taskInfo.LastRunTime)" -ForegroundColor Gray
Write-Host "  Last Result: $($taskInfo.LastTaskResult)" -ForegroundColor Gray
Write-Host ""

Write-Host "Setup Complete!" -ForegroundColor Green
Write-Host ""
Write-Host "Task Details:" -ForegroundColor Cyan
Write-Host "  Name: $taskName" -ForegroundColor Gray
Write-Host "  Trigger: At user logon" -ForegroundColor Gray
Write-Host "  User: $currentUser" -ForegroundColor Gray
Write-Host "  Node: $nodePath" -ForegroundColor Gray
Write-Host "  Script: $scriptPath" -ForegroundColor Gray
Write-Host ""
Write-Host "Management Commands:" -ForegroundColor Cyan
Write-Host "  Check status:  Get-ScheduledTask SplintGeoProcessor" -ForegroundColor Gray
Write-Host "  Start task:    Start-ScheduledTask SplintGeoProcessor" -ForegroundColor Gray
Write-Host "  Stop task:     Stop-ScheduledTask SplintGeoProcessor" -ForegroundColor Gray
Write-Host "  View logs:     Get-Content ~\SplintFactoryFiles\logs\geo-processor.log -Tail 50" -ForegroundColor Gray
Write-Host ""
