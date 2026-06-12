# Workaround for Rhino on Windows Server: tricks Windows into reporting a
# workstation SKU so Rhino's installer/license/runtime checks behave.
#
# Source: https://discourse.mcneel.com/ (Rhino on EC2 Windows Server thread)
#
# IMPORTANT:
#   - Reboot required after applying.
#   - Windows Update may revert ProductName/EditionID; re-run if Rhino breaks
#     after a cumulative update.
#   - This is NOT supported by McNeel.
#   - Run in elevated PowerShell.
#
# Usage:
#   .\rhino-server-workaround.ps1                  # back up + apply (default)
#   .\rhino-server-workaround.ps1 -Action Backup   # save current values only
#   .\rhino-server-workaround.ps1 -Action Apply    # apply workaround values
#   .\rhino-server-workaround.ps1 -Action Revert   # restore from backup file

#Requires -RunAsAdministrator

param(
    [ValidateSet('Apply', 'Backup', 'Revert')]
    [string]$Action = 'Apply',

    [string]$BackupFile = "$env:USERPROFILE\rhino-server-workaround-backup.json"
)

$ErrorActionPreference = 'Stop'

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
    Write-Host "Done. REBOOT the VM for the changes to take effect." -ForegroundColor Yellow
    Write-Host "Revert with: .\rhino-server-workaround.ps1 -Action Revert" -ForegroundColor DarkGray
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
    Write-Host ""
    Write-Host "Done. REBOOT the VM for the changes to take effect." -ForegroundColor Yellow
}

Write-Host "Current values:" -ForegroundColor Cyan
(Read-Current).GetEnumerator() | ForEach-Object {
    Write-Host ("  {0,-18} = {1}" -f $_.Key, $_.Value.Value)
}
Write-Host ""

switch ($Action) {
    'Backup' { Save-Backup }
    'Apply'  { Apply-Workaround }
    'Revert' { Revert-Workaround }
}
