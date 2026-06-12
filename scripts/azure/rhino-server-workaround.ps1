# Workaround for Rhino on Windows Server: tricks Windows into reporting a
# workstation SKU so Rhino's installer/license/runtime checks behave.
#
# Source: https://discourse.mcneel.com/ (Rhino on EC2 Windows Server thread)
#
# Windows re-stamps the Windows NT\CurrentVersion values on every boot, so
# `-Action Apply` alone wears off. Use `-Action Persist` to also register an
# AtStartup scheduled task that re-applies the values on every boot, BEFORE
# the SplintGeoProcessor scheduled task (which is AtLogOn) fires Rhino.
#
# IMPORTANT:
#   - Not officially supported by McNeel.
#   - Reboot required for changes to take effect (and re-confirm persistence).
#   - Run in elevated PowerShell.
#
# Usage:
#   .\rhino-server-workaround.ps1                      # apply now (one-shot)
#   .\rhino-server-workaround.ps1 -Action Backup       # snapshot current values
#   .\rhino-server-workaround.ps1 -Action Apply        # apply workaround values
#   .\rhino-server-workaround.ps1 -Action Persist      # apply + register boot task
#   .\rhino-server-workaround.ps1 -Action Unpersist    # remove boot task only
#   .\rhino-server-workaround.ps1 -Action Revert       # restore from backup + remove boot task

#Requires -RunAsAdministrator

param(
    [ValidateSet('Apply', 'Backup', 'Revert', 'Persist', 'Unpersist')]
    [string]$Action = 'Apply',

    [string]$BackupFile = "$env:USERPROFILE\rhino-server-workaround-backup.json"
)

$ErrorActionPreference = 'Stop'

$TaskName = 'RhinoServerWorkaround'

$keys = @(
    @{ Path = 'HKLM:\SYSTEM\CurrentControlSet\Control\ProductOptions';      Name = 'ProductType' },
    @{ Path = 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion';         Name = 'ProductName' },
    @{ Path = 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion';         Name = 'EditionID' },
    @{ Path = 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion';         Name = 'InstallationType' }
)

# Workaround values per the McNeel forum thread
$workaroundValues = @{
    'ProductType'      = 'WinNT'
    'ProductName'      = 'Windows 10 Pro'
    'EditionID'        = 'Professional'
    'InstallationType' = 'Client'
}

function Read-Current {
    $snapshot = [ordered]@{}
    foreach ($k in $keys) {
        $val = (Get-ItemProperty -Path $k.Path -Name $k.Name -ErrorAction SilentlyContinue).$($k.Name)
        $snapshot[$k.Name] = @{
            Path  = $k.Path
            Value = $val
        }
    }
    return $snapshot
}

function Save-Backup {
    $snapshot = Read-Current
    $snapshot | ConvertTo-Json -Depth 5 | Set-Content -Path $BackupFile -Encoding utf8
    Write-Host "Backup written to: $BackupFile" -ForegroundColor Green
    $snapshot.GetEnumerator() | ForEach-Object {
        Write-Host ("  {0,-18} = {1}" -f $_.Key, $_.Value.Value)
    }
}

function Apply-Workaround {
    if (-not (Test-Path $BackupFile)) {
        Write-Host "No backup found - creating one first." -ForegroundColor Yellow
        Save-Backup
    } else {
        Write-Host "Backup already exists at $BackupFile (preserving)." -ForegroundColor Yellow
    }

    Write-Host ""
    Write-Host "Applying workaround values..." -ForegroundColor Cyan
    foreach ($k in $keys) {
        $newVal = $workaroundValues[$k.Name]
        Set-ItemProperty -Path $k.Path -Name $k.Name -Value $newVal
        Write-Host ("  {0,-18} -> {1}" -f $k.Name, $newVal) -ForegroundColor Green
    }
    Write-Host ""
    Write-Host "Done. REBOOT the VM for the changes to take full effect." -ForegroundColor Yellow
}

function Revert-Workaround {
    if (-not (Test-Path $BackupFile)) {
        throw "Backup file not found: $BackupFile"
    }
    $snapshot = Get-Content $BackupFile -Raw | ConvertFrom-Json
    Write-Host "Restoring from $BackupFile..." -ForegroundColor Cyan
    foreach ($k in $keys) {
        $orig = $snapshot.$($k.Name).Value
        if ($null -eq $orig) {
            Write-Host ("  {0,-18}  (no original value recorded - skipping)" -f $k.Name) -ForegroundColor Yellow
            continue
        }
        Set-ItemProperty -Path $k.Path -Name $k.Name -Value $orig
        Write-Host ("  {0,-18} -> {1}" -f $k.Name, $orig) -ForegroundColor Green
    }

    # Also remove the boot task if present
    if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        Write-Host "Removed boot task '$TaskName'." -ForegroundColor Green
    }

    Write-Host ""
    Write-Host "Done. REBOOT the VM for the changes to take full effect." -ForegroundColor Yellow
}

function Persist-Workaround {
    # Apply now first, so this boot is also covered
    Apply-Workaround

    # Build a single inline PowerShell command that re-applies the four values.
    # Inlined (not referencing this script) so the task doesn't depend on the
    # repo being checked out at any particular path.
    $cmdParts = @()
    foreach ($k in $keys) {
        $newVal = $workaroundValues[$k.Name]
        $cmdParts += "Set-ItemProperty -Path '$($k.Path)' -Name '$($k.Name)' -Value '$newVal' -ErrorAction SilentlyContinue"
    }
    $inlineCmd = $cmdParts -join '; '

    $action = New-ScheduledTaskAction `
        -Execute 'powershell.exe' `
        -Argument "-NoProfile -ExecutionPolicy Bypass -Command `"$inlineCmd`""

    # AtStartup fires during boot as SYSTEM, BEFORE any user logon. That guarantees
    # this runs before the SplintGeoProcessor AtLogOn task launches Rhino.
    $trigger = New-ScheduledTaskTrigger -AtStartup

    $principal = New-ScheduledTaskPrincipal `
        -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest

    $settings = New-ScheduledTaskSettingsSet `
        -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
        -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 1)

    if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    }

    Register-ScheduledTask -TaskName $TaskName `
        -Action $action -Trigger $trigger `
        -Principal $principal -Settings $settings `
        -Description 'Re-apply Rhino-on-Server registry workaround at boot' | Out-Null

    Write-Host ""
    Write-Host "Boot task '$TaskName' registered (runs as SYSTEM AtStartup)." -ForegroundColor Green
    Write-Host "Reboot to verify it fires before SplintGeoProcessor warms up Rhino." -ForegroundColor Yellow
}

function Unpersist-Workaround {
    if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        Write-Host "Removed boot task '$TaskName'." -ForegroundColor Green
    } else {
        Write-Host "Boot task '$TaskName' not present - nothing to remove." -ForegroundColor Yellow
    }
}

Write-Host "Current values:" -ForegroundColor Cyan
(Read-Current).GetEnumerator() | ForEach-Object {
    Write-Host ("  {0,-18} = {1}" -f $_.Key, $_.Value.Value)
}
Write-Host ""

switch ($Action) {
    'Backup'    { Save-Backup }
    'Apply'     { Apply-Workaround }
    'Revert'    { Revert-Workaround }
    'Persist'   { Persist-Workaround }
    'Unpersist' { Unpersist-Workaround }
}
