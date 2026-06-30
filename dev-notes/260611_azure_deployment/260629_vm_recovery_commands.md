# Azure VM Recovery Commands (2026-06-29)

Context: `splintgeo1` in resource group `splintgeo1-rg` showed as deallocated after budget/payment changes.

## 1. Check subscription and VM state

```bash
az account show --query "{name:name, state:state, subscriptionId:id}" -o table

az vm get-instance-view \
  --resource-group splintgeo1-rg \
  --name splintgeo1 \
  --query "{size:hardwareProfile.vmSize, power:instanceView.statuses[?starts_with(code, 'PowerState')].displayStatus | [0], provisioning:instanceView.statuses[?starts_with(code, 'ProvisioningState')].displayStatus | [0]}" \
  -o table
```

## 2. Start the VM and confirm it is running

```bash
az vm start --resource-group splintgeo1-rg --name splintgeo1

az vm get-instance-view \
  --resource-group splintgeo1-rg \
  --name splintgeo1 \
  --query "{size:hardwareProfile.vmSize, power:instanceView.statuses[?starts_with(code, 'PowerState')].displayStatus | [0]}" \
  -o table
```

## 3. Verify processor logs after boot (optional)

```bash
sleep 45
ssh -o ConnectTimeout=15 splintgeo1 \
  "tail -n 8 ~/SplintFactoryFiles/logs/processor-$(date +%Y-%m-%d).log 2>/dev/null || echo 'log not yet; autologon may still be in progress'"
```

## Quick restore one-liner

```bash
az vm start -g splintgeo1-rg -n splintgeo1
```
