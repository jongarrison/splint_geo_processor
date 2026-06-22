# Rhino Keep-Warm Lease (260622)

Single source of truth for the Rhino keep-warm feature on splintgeo1.

## Goal

Keep Rhino warm only when a user is about to submit a job, to minimize Core
Hour Billing while preserving fast response when users are active.

## Signal

A visit to `/design-jobs/new?designId={id}` extends a 10-minute warm lease.

## State

### splint_factory
Single `warmUntil DateTime?` column on existing `ProcessorHeartbeat` row.

### splint_geo_processor
Single local field: `keepWarmUntilMs: number` (in-memory; resets on restart).

## Lease semantics

- Lease duration: **10 minutes** per signal.
- Server-side merge: `warmUntil = max(current warmUntil, now + 10min)`.
- Wire format: relative seconds (`keepWarmForSeconds`), so server clock skew
  doesn't matter to the processor.

## Wire protocol

### `POST /api/design-processing/keep-warm`
- Session auth.
- No body.
- Extends lease by 10 minutes.
- Idempotent; fire-and-forget from client.
- Returns 204.

### `GET /api/design-processing/next-job` (modified)
Add `keepWarmForSeconds: number` to both 200 (job) and 404 (no jobs) responses.
Computed as `max(0, (warmUntil - now) / 1000)`.

## Behavior

### splint_factory
1. `/design-jobs/new` page calls `POST /api/design-processing/keep-warm` on
   mount (client-side `useEffect`, fire-and-forget).
   - Must be a client POST, not server-side page render: Next.js prefetches
     pages on link hover, and we don't want those to count as warm signals.

### splint_geo_processor
Per poll cycle:
1. Receive poll response (200 or 404).
2. If `keepWarmForSeconds > 0`:
   `keepWarmUntilMs = max(keepWarmUntilMs, Date.now() + keepWarmForSeconds * 1000)`.
3. Compute `effectiveKeepRhinoAlive = config.keepRhinoAlive || Date.now() < keepWarmUntilMs`.
4. If `effectiveKeepRhinoAlive && !rhinoRunning` → warm Rhino proactively.
5. If `!effectiveKeepRhinoAlive && rhinoRunning` (and not mid-job) → close Rhino.
6. Pass `effectiveKeepRhinoAlive` into `runPipeline(...)` so end-of-job
   shutdown logic in `pipeline.ts` continues to work correctly.

## Config: `KEEP_RHINO_ALIVE`

**Retained** as a force-always-warm override:
- `KEEP_RHINO_ALIVE=true` → Rhino always warm; lease has no effect.
- `KEEP_RHINO_ALIVE=false` → lease drives warm/cold state.

| Host          | Setting | Rationale                          |
|---------------|---------|------------------------------------|
| lazyboy2000   | `true`  | Dedicated license, no per-hour cost |
| splintgeo1    | `false` | Core Hour Billing — minimize uptime |

## Implementation order

1. Prisma migration (`warmUntil` on `ProcessorHeartbeat`).
2. `lib/geo-processor-health.ts`: `extendProcessorWarmLease(ms)`,
   `getProcessorKeepWarmRemainingMs()`.
3. New `POST /api/design-processing/keep-warm` route.
4. Modify `GET /api/design-processing/next-job` to include
   `keepWarmForSeconds`.
5. Processor changes: `keepWarmUntilMs` field, `effectiveKeepRhinoAlive`
   computation, proactive warm/close in poll loop, refactor
   `closeRhino(...)` helper out of `pipeline.ts`.
6. Page mount: client-side `POST` call from `src/app/design-jobs/new/page.tsx`.

## Out of scope

- Hard cap: not needed; page-mount is the only signal source, lease is
  bounded by user activity.
- Per-user / per-design lease tracking: not needed.
- Suspend/resume Rhino process: not pursuing.
- New processor-side endpoints: lease comes back on existing poll response.

## Summary of work done
All seven implementation steps are done across both repos.

splint_factory
* schema.prisma:212-221 — added warmUntil DateTime? to ProcessorHeartbeat
* prisma/migrations/20260622000000_add_processor_warm_until/migration.sql — new migration adding the column
* src/lib/geo-processor-health.ts — added PROCESSOR_KEEP_WARM_LEASE_MS constant, warmUntil on snapshot type, and two helpers: extendProcessorWarmLease() and getProcessorKeepWarmRemainingSeconds()
* src/app/api/design-processing/keep-warm/route.ts — new POST endpoint (session-auth, no body, returns 204)
* src/app/api/design-processing/next-job/route.ts — now returns keepWarmForSeconds on both 200 and 404 responses
* src/app/design-jobs/new/page.tsx — fires POST /api/design-processing/keep-warm from useEffect on mount

splint_geo_processor
* src/processors/pipeline.ts — extracted closeRhino() and isRhinoRunning() helpers; refactored end-of-job shutdown to use them
* src/processors/processor.ts — added keepWarmUntilMs field, three private methods (ingestKeepWarmFromResponse, effectiveKeepRhinoAlive, reconcileRhinoToLease), called from the poll loop; runPipeline now receives effectiveKeepRhinoAlive() so end-of-job shutdown respects an active lease
* KEEP_RHINO_ALIVE env var retained as a force-always-warm override per the design doc (true on lazyboy2000, false on splintgeo1)