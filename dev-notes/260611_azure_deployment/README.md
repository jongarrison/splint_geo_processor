# Azure Windows VM Deployment for splint_geo_processor

This is a parallel production deployment to `lazyboy2000.local`, running on a
Microsoft Azure Windows Server VM. Goal: validate cloud-hosted geometry
processing as an alternative to the on-prem Windows box.

## Architecture

Same wrapper-based design as `lazyboy2000.local`:
- Scheduled task at logon launches `scripts/run-processor.cmd`
- Wrapper relaunches `node dist/index.js` after any exit (10-second backoff)
- Auto-logon enabled so the task fires after VM boot/restart
- SSH (Git Bash shell) on port 22 so the existing `win-deploy-prod-ssh.sh` works
  with only a hostname change

## VM specs (initial experiment)

| Resource    | Value                                    | Why                                   |
|-------------|------------------------------------------|---------------------------------------|
| VM size     | `Standard_D4s_v7` (4 vCPU, 16 GB RAM)    | Matches Rhino's recommended minimum   |
| OS          | Windows Server 2022 Datacenter Azure Ed. | Standard supported Windows base       |
| OS disk     | 128 GB Premium SSD                       | Rhino + Bambu Studio + archive room   |
| GPU         | None                                     | Headless geometry shouldn't need one  |
| Region      | eastus                                   | (selected during provisioning)        |
| Networking  | RDP+SSH locked to provisioning IP        | Add other IPs in portal as needed     |

Approximate cost: ~$140/mo on-demand. Deallocate when not in use to stop billing.

### SKU availability

Azure capacity varies by region. If `provision-vm.sh` fails with
`SkuNotAvailable`, find an alternative 4-vCPU SKU with no restrictions:

```bash
az vm list-skus --location <region> --resource-type virtualMachines \
  --query "[?starts_with(name,'Standard_D4') && length(restrictions)==\`0\`].name" -o tsv
```

Then override:
```bash
VM_SIZE=Standard_D4as_v5 ./scripts/azure/provision-vm.sh
```

Common fallbacks: `Standard_D4as_v5` (AMD), `Standard_D4ds_v7` (with local SSD),
`Standard_D4s_v4` (older Intel, plentiful).

## Setup procedure

### 1. Provision VM (from Mac)

Prerequisites:
- Azure CLI: `brew install azure-cli`
- `az login` (opens browser)

Run:
```bash
cd splint_geo_processor
./scripts/azure/provision-vm.sh
```

The script:
- Creates resource group `splintgeo1-rg`
- Creates VM `splintgeo1` with public DNS `splintgeo1.eastus.cloudapp.azure.com`
- Adds NSG rules for RDP (3389) and SSH (22), locked to the Mac's current public IP
- Prompts for the admin password (must be 12-72 chars with 3 of 4: upper/lower/digit/special)

### 2. RDP in and install GUI tools

From Mac: install **Microsoft Remote Desktop** (App Store), add PC at the
provisioned FQDN, sign in with `splintadmin` and the password from step 1.

On the VM:
1. Install **Rhino 8** from rhino3d.com.
   - **Licensing caveat:** standalone Rhino licenses are tied to a hardware
     fingerprint that changes if the VM is resized or redeployed. Use Cloud Zoo
     (subscription) or a dedicated VM license. Confirm with McNeel that
     unattended automated geometry generation in Azure is permitted under your
     license type.
2. Install **Bambu Studio** from bambulab.com.
3. Open Grasshopper at least once and let plugins auto-install.

### 3. Bootstrap the VM (elevated PowerShell on the VM)

```powershell
# After the repo is publicly readable (or copy bootstrap-vm.ps1 manually):
iwr -UseBasicParsing https://raw.githubusercontent.com/jongarrison/splint_geo_processor/main/scripts/azure/bootstrap-vm.ps1 | iex
```

This installs Chocolatey, Node.js LTS, Git, OpenSSH Server (with Git Bash as
default shell), sets `ENV_MODE=production`, and clones the repo to
`C:\Users\splintadmin\work\splint_geo_processor`.

### 4. SSH key + secrets

On Mac:
```bash
cat ~/.ssh/id_ed25519.pub
```
Paste the output into `C:\Users\splintadmin\.ssh\authorized_keys` on the VM.

Create `C:\Users\splintadmin\work\splint_geo_processor\.env` containing at
minimum:
```
SF_API_KEY=<production-api-key>
```

Verify `.env.platform.win` has the correct paths for Rhino 8 and Bambu Studio
on this VM (defaults from the repo should work for a stock install).

### 5. Auto-logon (so the scheduled task runs after reboots)

The scheduled task uses `-AtLogOn` trigger, which means it only fires when a
user logs in. For the VM to recover from reboots without manual RDP, enable
auto-logon for `splintadmin`. There's no built-in PS cmdlet; use Sysinternals
**Autologon.exe** (recommended, encrypts the password in the registry):

```powershell
# On the VM, in elevated PowerShell:
choco install sysinternals -y
autologon.exe
# Fill in: User=splintadmin, Domain=<vm-name>, Password=<admin-password>, Enable
```

### 6. Register the scheduled task

```powershell
cd C:\Users\splintadmin\work\splint_geo_processor
.\scripts\setup-windows-startup.ps1
```

### 7. Verify from Mac

```bash
ssh splintadmin@splintgeo1.eastus.cloudapp.azure.com
# Then on the VM:
tail -f ~/SplintFactoryFiles/logs/processor-$(date +%Y-%m-%d).log
```

## Future deploys

To deploy code updates to the Azure VM, copy `win-deploy-prod-ssh.sh` to a new
script (e.g. `win-deploy-azure-ssh.sh`) and change `WINDOWS_HOST` to the FQDN.
Or pass the host as an env var if we want one script for both targets.

## Operations

### Stop billing without losing the VM

```bash
az vm deallocate --resource-group splintgeo1-rg --name splintgeo1
```

### Start it again

```bash
az vm start --resource-group splintgeo1-rg --name splintgeo1
```

### Update allowed IP (e.g. travelling)

In the portal: `splintgeo1NSG` → AllowRDP / AllowSSH → edit source address.

### Tear it all down

```bash
az group delete --name splintgeo1-rg --yes
```

## Open questions / things to confirm

- [ ] Rhino licensing model that survives Azure VM hardware fingerprint changes
- [ ] Whether headless Rhino on D4s_v5 (no GPU) actually executes our
      Grasshopper scripts — some plugins require real OpenGL. If broken,
      upgrade to `NV4as_v3` (small AMD GPU SKU) at ~3x cost.
- [ ] Whether splint_factory production traffic to this VM is acceptable
      latency-wise from `eastus` (vs `westus2/3` if factory is west-coast).
- [ ] Decide on a deploy script naming convention if we end up running both
      lazyboy2000.local and the Azure VM in parallel.

## Related files

- [provision-vm.sh](../scripts/azure/provision-vm.sh) — Azure CLI VM creation
- [bootstrap-vm.ps1](../scripts/azure/bootstrap-vm.ps1) — On-VM software setup
- [setup-windows-startup.ps1](../scripts/setup-windows-startup.ps1) — Scheduled task
- [run-processor.cmd](../scripts/run-processor.cmd) — Node restart wrapper
- [251210_windows_setup_notes.md](./251210_windows_setup_notes.md) — Original lazyboy2000.local setup
