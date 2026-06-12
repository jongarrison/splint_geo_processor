
Results of running the setup script:

% ./scripts/azure/provision-vm.sh
Locking RDP/SSH access to: 24.113.173.165
Enter VM admin password (12-72 chars, complex): 
Confirm password: 

About to create:
  Resource group : splintgeo1-rg
  Region         : eastus
  VM             : splintgeo1 (Standard_D4s_v7)
  Admin user     : splintadmin
  OS disk        : 128 GB Premium SSD
  Source IP      : 24.113.173.165 (RDP+SSH only from this IP)

Proceed? [y/N]: y

Creating resource group...
Creating VM (this takes 2-5 minutes)...
Consider upgrading security for your workloads using Azure Trusted Launch VMs. To know more about Trusted Launch, please visit https://aka.ms/TrustedLaunch.
Configuring NSG (RDP + SSH from 24.113.173.165 only)...

Hostname : splintgeo1.eastus.cloudapp.azure.com
RDP from Mac:
  Use Microsoft Remote Desktop, add PC: splintgeo1.eastus.cloudapp.azure.com
  Username: splintadmin

Next steps (in order):
  1. RDP into the VM.
  2. Install Rhino 8 (download from rhino3d.com) and sign in.
  3. Install Bambu Studio (download from bambulab.com).
  4. Open Grasshopper once and let plugins auto-install.
  5. Open elevated PowerShell on the VM and run:

     iwr -UseBasicParsing https://raw.githubusercontent.com/jongarrison/splint_geo_processor/main/scripts/azure/bootstrap-vm.ps1 | iex

     (Or copy bootstrap-vm.ps1 manually if your repo is private.)

  6. After bootstrap completes, copy your Mac SSH public key into
     C:\Users\splintadmin\.ssh\authorized_keys on the VM.
     Get your key from Mac with:  cat ~/.ssh/id_ed25519.pub

  7. Test SSH from Mac:  ssh splintadmin@splintgeo1.eastus.cloudapp.azure.com

To stop billing (deallocate):
  az vm deallocate --resource-group splintgeo1-rg --name splintgeo1

To delete everything:
  az group delete --name splintgeo1-rg --yes