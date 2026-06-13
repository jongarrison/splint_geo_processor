# Azure Windows VM Deployment for splint_geo_processor

Parallel production deployment to `lazyboy2000.local`, running on a Microsoft
Azure Windows Server VM.

## Architecture

Same wrapper-based design as `lazyboy2000.local`:
- Scheduled task at logon launches `scripts/run-processor.cmd`
- Wrapper relaunches `node dist/index.js` after any exit (10s backoff)
- Auto-logon enabled so the task fires after VM boot
- OpenSSH on port 22 with Git Bash as default shell, so `win-deploy-prod-ssh.sh`
  works against this VM with only a hostname change

## VM specs

| Resource    | Value                                    |
|-------------|------------------------------------------|
| VM size     | `Standard_D4s_v7` (4 vCPU, 16 GB RAM)    |
| OS          | Windows Server 2022 Datacenter Azure Ed. |
| OS disk     | 128 GB Premium SSD                       |
| GPU         | None                                     |
| Region      | eastus                                   |
| Networking  | RDP+SSH NSG locked to provisioning IP    |

~$140/mo on-demand. `az vm deallocate` to stop billing.

If `provision-vm.sh` fails with `SkuNotAvailable`, override `VM_SIZE` (e.g.
`VM_SIZE=Standard_D4as_v5 ./scripts/azure/provision-vm.sh`).

## Setup procedure

### 1. Provision (from Mac)

```bash
brew install azure-cli && az login
cd splint_geo_processor
./scripts/azure/provision-vm.sh
```

Creates `splintgeo1-rg` / `splintgeo1` with DNS
`splintgeo1.eastus.cloudapp.azure.com`. Prompts for admin password.

### 2. RDP in and install GUI tools

Install **Microsoft Remote Desktop** on Mac, connect as `splintadmin`.

On the VM:
1. Install **Rhino 8** (rhino3d.com).
2. **License via Core Hour Billing** (single-computer Rhino licenses are NOT
   supported on Windows Server). Follow:
   <https://developer.rhino3d.com/guides/compute/core-hour-billing/#single-computer-licensing-not-supported>
   Generate a token in your Rhino account, then in elevated PowerShell:
   ```powershell
   [System.Environment]::SetEnvironmentVariable('RHINO_TOKEN', '<your-token>', 'Machine')
   ```
   Reboot or fully restart Rhino afterwards so it re-reads the env var.
3. Launch Rhino once and confirm it activates without a license dialog.
4. **Critical:** in Rhino, `Tools > Options > General > Command list at startup`,
   add `StartScriptServer`. Without this the processor cannot target Rhino —
   `RhinoCode.exe list --json` returns `[]` and jobs fail with
   "Rhino did not start successfully after launch attempts".
5. Install **Bambu Studio** (bambulab.com). Open it once and dismiss any
   first-run dialogs.
6. Open Grasshopper at least once to let plugins auto-install.

### 3. Bootstrap the VM (elevated PowerShell)

In Git Bash on the VM:
```bash
mkdir -p /c/Users/splintadmin/work && cd /c/Users/splintadmin/work
git clone https://github.com/jongarrison/splint_geo_processor.git
```

Then in elevated PowerShell:
```powershell
cd C:\Users\splintadmin\work\splint_geo_processor\scripts\azure
.\bootstrap-vm.ps1
```

The script installs Chocolatey, Node.js LTS, Git (skipped if already present),
and OpenSSH Server; sets Git Bash as the SSH default shell; opens firewall port
22; sets `ENV_MODE=production` (User scope); and creates
`C:\ProgramData\ssh\administrators_authorized_keys` with locked-down ACLs.

### 4. SSH key + secrets (on the VM)

`splintadmin` is in the Administrators group, so Windows OpenSSH uses
`C:\ProgramData\ssh\administrators_authorized_keys` and **ignores** any
per-user `~/.ssh/authorized_keys`.

In elevated PowerShell:
```powershell
$key = '<paste output of `cat ~/.ssh/id_ed25519.pub` from Mac>'
Add-Content -Path C:\ProgramData\ssh\administrators_authorized_keys -Value $key
Restart-Service sshd
```

Create `C:\Users\splintadmin\work\splint_geo_processor\.env`:
```
SF_API_KEY=<production-api-key>
```

`.env.platform.win` is committed and should already point at the default
Rhino 8 / Bambu Studio install paths — verify if anything was installed
non-standard.

### 5. Build, autologon, scheduled task

From Mac (now that SSH key auth works):
```bash
ssh splintgeo1 "cd /c/Users/splintadmin/work/splint_geo_processor && npm install && npm run build"
```

On the VM, in elevated PowerShell:
```powershell
choco install sysinternals -y
& "C:\ProgramData\chocolatey\lib\sysinternals\tools\autologon.exe"
# Dialog: User=splintadmin, Domain=splintgeo1, Password=<admin-pw>, click Enable

cd C:\Users\splintadmin\work\splint_geo_processor
.\scripts\setup-windows-startup.ps1
```

Reboot the VM to verify the full chain
(boot → autologon → task at logon → wrapper → node).

### 6. Verify (from Mac)

```bash
ssh splintgeo1 "tail -f ~/SplintFactoryFiles/logs/processor-\$(date +%Y-%m-%d).log"
```

Expect polling activity within a few seconds.

## Future deploys

`win-deploy-prod-ssh.sh` accepts a `WINDOWS_HOST` env var override; default is
`lazyboy2000.local`. To deploy to the Azure VM:

```bash
WINDOWS_HOST=splintgeo1 ./win-deploy-prod-ssh.sh
```

(Relies on the `~/.ssh/config` host alias for `splintgeo1`.)

## Operations

```bash
az vm deallocate --resource-group splintgeo1-rg --name splintgeo1   # stop billing
az vm start      --resource-group splintgeo1-rg --name splintgeo1   # resume
az group delete  --name splintgeo1-rg --yes                         # tear down
```

To update allowed inbound IPs, edit the `AllowRDP` / `AllowSSH` rules in NSG
`splintgeo1NSG` in the Azure portal.

## Open questions

- [ ] Whether Core Hour Billing tokens survive deallocate/start cycles without
      user intervention.
- [ ] Whether headless `D4s_v7` (no GPU) survives all our Grasshopper plugins,
      or if we need an `NV4as_v3`-class SKU for OpenGL-dependent components.
- [ ] Latency from `eastus` to splint_factory production traffic if factory
      ends up west-coast.

## Related files

- [provision-vm.sh](../../scripts/azure/provision-vm.sh) — Azure CLI VM creation
- [bootstrap-vm.ps1](../../scripts/azure/bootstrap-vm.ps1) — On-VM software setup
- [setup-windows-startup.ps1](../../scripts/setup-windows-startup.ps1) — Scheduled task
- [run-processor.cmd](../../scripts/run-processor.cmd) — Node restart wrapper
- [251210_windows_setup_notes.md](./251210_windows_setup_notes.md) — Original lazyboy2000.local setup
