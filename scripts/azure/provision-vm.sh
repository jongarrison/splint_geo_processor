#!/bin/bash
#
# Provision an Azure Windows Server VM for splint_geo_processor.
# Run this from your Mac after `az login`. Idempotent for the most part:
# re-running with the same VM_NAME will fail at vm create (existing resource).
# To start fresh: az group delete --name "${VM_NAME}-rg" --yes --no-wait
#
# After this script completes:
#   1. RDP into the VM (Microsoft Remote Desktop on Mac)
#   2. Install Rhino 8 + Bambu Studio interactively
#   3. Run scripts/azure/bootstrap-vm.ps1 in elevated PowerShell on the VM
#

set -e

# ----- CONFIG -----
VM_NAME="splintgeo1"
RESOURCE_GROUP="${VM_NAME}-rg"
REGION="eastus"
VM_SIZE="Standard_D4s_v5"          # 4 vCPU, 16 GB RAM, ~$140/mo on-demand
ADMIN_USERNAME="splintadmin"
OS_DISK_SIZE_GB=128
IMAGE="MicrosoftWindowsServer:WindowsServer:2022-datacenter-azure-edition:latest"
# ------------------

# Detect this Mac's public IP for NSG lockdown
MY_IP=$(curl -s https://api.ipify.org)
if [[ -z "$MY_IP" ]]; then
    echo "Failed to detect public IP. Check internet connection." >&2
    exit 1
fi
echo "Locking RDP/SSH access to: $MY_IP"

# Prompt for password (not echoed). Azure requires 12-72 chars with 3 of 4:
# uppercase, lowercase, digit, special.
read -s -p "Enter VM admin password (12-72 chars, complex): " ADMIN_PASSWORD
echo ""
read -s -p "Confirm password: " ADMIN_PASSWORD2
echo ""
if [[ "$ADMIN_PASSWORD" != "$ADMIN_PASSWORD2" ]]; then
    echo "Passwords do not match." >&2
    exit 1
fi

echo ""
echo "About to create:"
echo "  Resource group : $RESOURCE_GROUP"
echo "  Region         : $REGION"
echo "  VM             : $VM_NAME ($VM_SIZE)"
echo "  Admin user     : $ADMIN_USERNAME"
echo "  OS disk        : ${OS_DISK_SIZE_GB} GB Premium SSD"
echo "  Source IP      : $MY_IP (RDP+SSH only from this IP)"
echo ""
read -p "Proceed? [y/N]: " confirm
if [[ "$confirm" != "y" && "$confirm" != "Y" ]]; then
    echo "Aborted."
    exit 0
fi

echo ""
echo "Creating resource group..."
az group create --name "$RESOURCE_GROUP" --location "$REGION" --output none

echo "Creating VM (this takes 2-5 minutes)..."
az vm create \
    --resource-group "$RESOURCE_GROUP" \
    --name "$VM_NAME" \
    --image "$IMAGE" \
    --size "$VM_SIZE" \
    --admin-username "$ADMIN_USERNAME" \
    --admin-password "$ADMIN_PASSWORD" \
    --public-ip-sku Standard \
    --public-ip-address-dns-name "$VM_NAME" \
    --os-disk-size-gb "$OS_DISK_SIZE_GB" \
    --storage-sku Premium_LRS \
    --nsg-rule NONE \
    --output none

NSG_NAME="${VM_NAME}NSG"

echo "Configuring NSG (RDP + SSH from $MY_IP only)..."
az network nsg rule create \
    --resource-group "$RESOURCE_GROUP" \
    --nsg-name "$NSG_NAME" \
    --name AllowRDP \
    --priority 100 \
    --source-address-prefixes "$MY_IP" \
    --destination-port-ranges 3389 \
    --protocol Tcp \
    --access Allow \
    --output none

az network nsg rule create \
    --resource-group "$RESOURCE_GROUP" \
    --nsg-name "$NSG_NAME" \
    --name AllowSSH \
    --priority 110 \
    --source-address-prefixes "$MY_IP" \
    --destination-port-ranges 22 \
    --protocol Tcp \
    --access Allow \
    --output none

FQDN="${VM_NAME}.${REGION}.cloudapp.azure.com"

echo ""
echo "================================================================"
echo "VM provisioned successfully."
echo "================================================================"
echo ""
echo "Hostname : $FQDN"
echo "RDP from Mac:"
echo "  Use Microsoft Remote Desktop, add PC: $FQDN"
echo "  Username: $ADMIN_USERNAME"
echo ""
echo "Next steps (in order):"
echo "  1. RDP into the VM."
echo "  2. Install Rhino 8 (download from rhino3d.com) and sign in."
echo "  3. Install Bambu Studio (download from bambulab.com)."
echo "  4. Open Grasshopper once and let plugins auto-install."
echo "  5. Open elevated PowerShell on the VM and run:"
echo ""
echo "     iwr -UseBasicParsing https://raw.githubusercontent.com/jongarrison/splint_geo_processor/main/scripts/azure/bootstrap-vm.ps1 | iex"
echo ""
echo "     (Or copy bootstrap-vm.ps1 manually if your repo is private.)"
echo ""
echo "  6. After bootstrap completes, copy your Mac SSH public key into"
echo "     C:\\Users\\${ADMIN_USERNAME}\\.ssh\\authorized_keys on the VM."
echo "     Get your key from Mac with:  cat ~/.ssh/id_ed25519.pub"
echo ""
echo "  7. Test SSH from Mac:  ssh ${ADMIN_USERNAME}@${FQDN}"
echo ""
echo "To stop billing (deallocate):"
echo "  az vm deallocate --resource-group $RESOURCE_GROUP --name $VM_NAME"
echo ""
echo "To delete everything:"
echo "  az group delete --name $RESOURCE_GROUP --yes"
echo ""
