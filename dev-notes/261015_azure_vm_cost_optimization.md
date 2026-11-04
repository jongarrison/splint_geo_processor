# Azure VM Cost Optimization

## Recommendation

Test a new `Standard_B2as_v2` Windows VM with a 128 GB Standard SSD E10, then replace the existing VM if production job timings are acceptable.

| Resource | Existing | Recommended |
|---|---|---|
| VM | `Standard_D2s_v7` | `Standard_B2as_v2` |
| CPU / RAM | 2 vCPU / 8 GB | 2 vCPU / 8 GB |
| CPU model | Full performance continuously | 40% baseline with credit-based bursts to 100% |
| OS disk | Premium SSD P10 | Standard SSD E10 |
| Estimated bill with tax | About $200/month | About $80/month |

The current VM averaged roughly 1-1.75% CPU per day, with brief job peaks up to 80%. A B2as_v2 earns enough credits for this polling-heavy workload: a 24-second job using both vCPUs fully consumes about 0.48 credits, while the VM earns 48 credits per idle hour and can bank 1,152 credits.

The E10 and P10 both provide 500 baseline IOPS and 100 MB/s baseline throughput at 128 GB. E10 has lower burst IOPS and weaker latency guarantees, but large sequential STL and 3MF writes should see little difference.

## Alternatives

- `Standard_D2as_v5`: same CPU/RAM with continuous full CPU performance; approximately $33/month less than the existing VM, but much smaller savings than B2as_v2.
- `Standard_B2ms`: technically suitable, but its Bv1 family is end-of-life and unavailable for new deployment in this East US subscription.

## Migration Warning

Do not let both processors poll production simultaneously. Fetching the next job and marking it started are separate, non-atomic requests, so both processors could receive the same job. Benchmark the new VM with fixtures or a test queue, stop the old processor, and then enable production polling on the new VM.