# Activity-Aware Production Deployment

`win-deploy-prod-ssh.sh` now drains the processor before updating it:

1. Write a unique request token to `~/SplintFactoryFiles/control/deploy-requested`.
2. The processor finishes any active job, reaches the polling boundary, writes the same token to `deploy-ready`, and pauses polling.
3. The deploy script stops the scheduled task immediately, pulls code, installs dependencies, builds, removes both control files, and restarts the task.

The deploy script waits up to 3 minutes for an active job to finish. After acknowledgment, the processor waits only 60 seconds for the script to stop it; otherwise it removes the stale request and resumes polling. Override the 3-minute wait with `DRAIN_TIMEOUT_SECONDS`.

Each launch atomically replaces an earlier request, so retrying a failed deployment safely takes ownership of stale drain state. If the scheduled task is already stopped, the script clears stale control files and updates without waiting for an acknowledgment.

The first rollout to a processor that does not yet support draining must happen during a confirmed idle window:

```bash
SKIP_DRAIN=1 ./win-deploy-prod-ssh.sh
```

`SKIP_DRAIN=1` bypasses all activity protection and should only be used deliberately while the processor is known to be idle.
