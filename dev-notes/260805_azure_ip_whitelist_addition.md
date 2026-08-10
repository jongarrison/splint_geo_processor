## Azure allowlist update

Use the same flow for SSH and RDP. Only the port and rule name change.

1. Get the current public IP:
	`curl -4 -s https://ifconfig.me`
2. Find the existing NSG rule for the port:
	`az network nsg rule list -g splintgeo1-rg --nsg-name splintgeo1NSG --query "[?destinationPortRange=='22' || destinationPortRange=='3389' || contains(destinationPortRanges, '22') || contains(destinationPortRanges, '3389')].{name:name,source:sourceAddressPrefix,srcPrefixes:sourceAddressPrefixes,port:destinationPortRange}" -o table`
3. Update that rule so both IPs are allowed:
	`az network nsg rule update -g splintgeo1-rg --nsg-name splintgeo1NSG -n <rule-name> --source-address-prefixes 24.113.173.165 98.246.155.54`
4. Verify `sourceAddressPrefixes` contains both IPs.
