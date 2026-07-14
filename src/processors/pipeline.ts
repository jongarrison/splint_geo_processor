import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { execFile, exec, spawn } from 'node:child_process';
import { promisify } from 'node:util';
const execFileAsync = promisify(execFile);
const execAsync = promisify(exec);

// ES module equivalent of __dirname
const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

/**
 * Wait until a file is fully written and readable by a freshly-opened handle.
 *
 * Guards against a write/visibility race: a large geometry file can be visible to
 * this process (size already validated) yet not stably readable by a newly-spawned
 * child process. Observed on the Azure Windows host, where the large SizingRings
 * 3mf (~2.3MB) intermittently failed slicing with CLI_FILE_NOTFOUND (-3) while
 * small files sliced fine. Requires the size to hold steady across consecutive
 * polls and confirms the file can actually be opened for reading.
 *
 * @returns true when the file is stable and readable, false on timeout.
 */
async function waitForFileStableAndReadable(
  filePath: string,
  logger?: { info: (obj: any, msg?: string) => void; warn: (obj: any, msg?: string) => void },
  timeoutMs = 15_000
): Promise<boolean> {
  const start = Date.now();
  const pollMs = 150;
  const requiredStableChecks = 3; // consecutive equal, non-zero sizes
  let lastSize = -1;
  let stableCount = 0;

  while (Date.now() - start < timeoutMs) {
    let size = -1;
    try {
      size = fs.statSync(filePath).size;
    } catch {
      size = -1; // not visible yet
    }

    if (size > 0 && size === lastSize) {
      stableCount++;
      if (stableCount >= requiredStableChecks) {
        // Final gate: confirm a fresh read handle can open the file. This mirrors
        // what a newly-spawned child process does and catches transient locks
        // (e.g. antivirus scanning a just-created large file).
        try {
          const fd = fs.openSync(filePath, 'r');
          const probe = Buffer.alloc(1);
          fs.readSync(fd, probe, 0, 1, 0);
          fs.closeSync(fd);
          logger?.info({ filePath, size, waitMs: Date.now() - start }, 'Geometry file confirmed stable and readable');
          return true;
        } catch (err: any) {
          // Not yet readable (locked); reset and keep polling.
          stableCount = 0;
          logger?.warn({ filePath, error: err?.message }, 'Geometry file not yet readable (locked?) - retrying');
        }
      }
    } else {
      stableCount = 0;
      lastSize = size;
    }

    await new Promise(r => setTimeout(r, pollMs));
  }

  logger?.warn({ filePath, timeoutMs }, 'Timed out waiting for geometry file to become stable and readable');
  return false;
}

// ===========================
// Rhino/RhinoCode Utilities
// ===========================

/**
 * Execute rhinocode CLI with any subcommand.
 * Works cross-platform (macOS/Windows).
 * 
 * @param rhinoCodeCli - Path to rhinocode binary
 * @param args - Arguments to pass to rhinocode (e.g., ['pid'], ['command', 'script'], ['list', '--json'])
 * @param options - Optional execution options (timeout, env, etc.)
 * @param logger - Optional logger for diagnostics
 * @returns Promise resolving to stdout/stderr
 */
export async function executeRhinoCodeCli(
  rhinoCodeCli: string,
  args: string[],
  options: { timeout?: number; env?: NodeJS.ProcessEnv } = {},
  logger?: { info: (obj: any, msg?: string) => void; warn: (obj: any, msg?: string) => void }
): Promise<{ stdout: string; stderr: string }> {
  try {
    const { timeout = 30_000, env } = options;
    logger?.info({ rhinoCodeCli, args, timeout }, 'Executing rhinocode CLI');
    const { stdout, stderr } = await execFileAsync(rhinoCodeCli, args, { timeout, env });
    logger?.info({ stdout: stdout?.substring(0, 500), stderr: stderr?.substring(0, 500) }, 'rhinocode CLI command sent');
    return { stdout, stderr };
  } catch (err: any) {
    logger?.warn({ error: err?.message, stdout: err?.stdout, stderr: err?.stderr }, 'rhinocode CLI failed');
    throw new Error(`rhinocode CLI failed: ${err?.message}`);
  }
}

/**
 * Execute a Rhino command via rhinocode CLI.
 * Convenience wrapper around executeRhinoCodeCli for 'command' subcommand.
 * 
 * @param rhinoCodeCli - Path to rhinocode binary
 * @param commandString - Full Rhino command string (e.g., "! -_Grasshopper _Document _Open /path/to/file.gh _EnterEnd")
 * @param options - Optional execution options (timeout, env, etc.)
 * @param logger - Optional logger for diagnostics
 * @returns Promise resolving to stdout/stderr
 */
export async function executeRhinoCommand(
  rhinoCodeCli: string,
  commandString: string,
  options: { timeout?: number; env?: NodeJS.ProcessEnv } = {},
  logger?: { info: (obj: any, msg?: string) => void; warn: (obj: any, msg?: string) => void }
): Promise<{ stdout: string; stderr: string }> {
  return executeRhinoCodeCli(rhinoCodeCli, ['command', commandString], options, logger);
}

type PinoLogger = { info: (obj: any, msg?: string) => void; warn: (obj: any, msg?: string) => void };

const RHINO_HEALTH_PROBE_FILENAME = 'rhino_health_probe.json';

function resolveRhinoHealthProbeScriptPath(): string {
  const candidates = [
    path.resolve(process.cwd(), 'generators/src/rhino_health_probe.py'),
    path.resolve(__dirname, '../../generators/src/rhino_health_probe.py')
  ];

  for (const candidate of candidates) {
    if (fs.existsSync(candidate)) {
      return candidate;
    }
  }

  return candidates[0];
}

function isRhinoExecutionProbeEnabled(): boolean {
  return (process.env.RHINO_EXECUTION_PROBE_ENABLED ?? 'true').toLowerCase() !== 'false';
}

function getRhinoExecutionProbeWaitMs(): number {
  const raw = Number(process.env.RHINO_EXECUTION_PROBE_WAIT_MS ?? 5000);
  if (!Number.isFinite(raw) || raw < 1000) {
    return 5000;
  }
  return Math.floor(raw);
}

function getRhinoStartupProbeGraceMs(): number {
  const raw = Number(process.env.RHINO_STARTUP_PROBE_GRACE_MS ?? 3000);
  if (!Number.isFinite(raw) || raw < 0) {
    return 3000;
  }
  return Math.floor(raw);
}

function getRhinoStartupFailFastUnhealthyAttempts(): number {
  const raw = Number(process.env.RHINO_STARTUP_FAIL_FAST_UNHEALTHY_ATTEMPTS ?? 0);
  if (!Number.isFinite(raw) || raw < 0) {
    return 0;
  }
  return Math.floor(raw);
}

function getSplintOutboxDir(): string {
  const home = process.env.HOME || process.env.USERPROFILE || '.';
  return path.join(home, 'SplintFactoryFiles', 'outbox');
}

function shouldStartScriptServerOnRhinoLaunch(): boolean {
  return (process.env.RHINO_START_SCRIPT_SERVER_ON_LAUNCH ?? 'true').toLowerCase() !== 'false';
}

function getRhinoStartupMacro(): string {
  const configured = (process.env.RHINO_STARTUP_COMMAND ?? 'StartScriptServer').trim();
  return configured.length > 0 ? configured : 'StartScriptServer';
}

// Check if Rhino process exists at OS level (independent of rhinocode)
async function rhinoProcessExists(): Promise<boolean> {
  try {
    if (process.platform === 'win32') {
      const { stdout } = await execAsync('tasklist /FI "IMAGENAME eq Rhino.exe" /NH', { timeout: 5000 });
      return stdout.includes('Rhino.exe');
    } else {
      const { stdout } = await execAsync('pgrep -i rhino', { timeout: 5000 });
      return stdout.trim().length > 0;
    }
  } catch {
    return false;
  }
}

async function waitForRhinoExit(timeoutMs = 15_000, pollMs = 500): Promise<boolean> {
  const start = Date.now();
  while (Date.now() - start < timeoutMs) {
    const stillRunning = await rhinoProcessExists();
    if (!stillRunning) {
      return true;
    }
    await new Promise(resolve => setTimeout(resolve, pollMs));
  }
  return !(await rhinoProcessExists());
}

// Query how many targetable Rhino instances rhinocode can see.
async function getRhinoTargetCount(
  rhinoCodeCli: string,
  logger?: PinoLogger
): Promise<number> {
  try {
    const { stdout } = await executeRhinoCodeCli(rhinoCodeCli, ['list', '--json'], { timeout: 8000 }, logger);
    const raw = (stdout || '').trim();
    if (!raw) {
      return 0;
    }

    // Prefer direct JSON parse, but tolerate leading log lines before JSON.
    try {
      const parsed = JSON.parse(raw) as unknown;
      return Array.isArray(parsed) ? parsed.length : 0;
    } catch {
      const start = raw.indexOf('[');
      const end = raw.lastIndexOf(']');
      if (start >= 0 && end > start) {
        const parsed = JSON.parse(raw.slice(start, end + 1)) as unknown;
        return Array.isArray(parsed) ? parsed.length : 0;
      }
    }

    logger?.warn({ stdout: raw.substring(0, 300) }, 'Unable to parse rhinocode list output as JSON');
    return 0;
  } catch (err: any) {
    logger?.info({ error: err?.message }, 'rhinocode list query failed');
    return 0;
  }
}

async function checkRhinoExecutionProbe(
  rhinoCodeCli: string,
  startupExtraWaitMs: number,
  logger?: PinoLogger
): Promise<boolean> {
  const probeScriptPath = resolveRhinoHealthProbeScriptPath();
  if (!fs.existsSync(probeScriptPath)) {
    logger?.warn({ probeScriptPath }, 'Rhino execution probe script not found');
    return false;
  }

  const outboxDir = getSplintOutboxDir();
  const probeFilePath = path.join(outboxDir, RHINO_HEALTH_PROBE_FILENAME);

  try {
    fs.mkdirSync(outboxDir, { recursive: true });
  } catch (err: any) {
    logger?.warn({ outboxDir, error: err?.message }, 'Failed to create outbox directory for Rhino probe');
    return false;
  }

  try {
    if (fs.existsSync(probeFilePath)) {
      fs.unlinkSync(probeFilePath);
    }
  } catch (err: any) {
    logger?.warn({ probeFilePath, error: err?.message }, 'Failed to clear old Rhino probe file');
  }

  const probeStart = Date.now();
  try {
    await executeRhinoCodeCli(rhinoCodeCli, ['script', probeScriptPath], { timeout: 8000 }, logger);
  } catch (err: any) {
    logger?.warn({ error: err?.message }, 'Rhino execution probe command failed');
    return false;
  }

  const waitMs = getRhinoExecutionProbeWaitMs() + startupExtraWaitMs;
  while (Date.now() - probeStart <= waitMs) {
    try {
      if (fs.existsSync(probeFilePath)) {
        const stats = fs.statSync(probeFilePath);
        if (stats.mtimeMs >= probeStart - 200) {
          logger?.info({ probeFilePath, ageMs: Date.now() - stats.mtimeMs }, 'Rhino execution probe passed');
          return true;
        }
      }
    } catch (err: any) {
      logger?.warn({ error: err?.message }, 'Failed reading Rhino probe file');
    }

    await new Promise(resolve => setTimeout(resolve, 150));
  }

  logger?.warn({ probeFilePath, waitMs }, 'Rhino execution probe timed out waiting for marker file');
  return false;
}

// Windows-only check for GUI hung state ("Not Responding")
async function rhinoProcessResponding(logger?: PinoLogger): Promise<boolean> {
  if (process.platform !== 'win32') {
    return true;
  }

  try {
    const script = [
      '$proc = Get-Process -Name Rhino -ErrorAction SilentlyContinue | Select-Object -First 1',
      'if ($null -eq $proc) { Write-Output MISSING; exit 0 }',
      'if ($proc.Responding -eq $true) { Write-Output RESPONDING; exit 0 }',
      'if ($proc.Responding -eq $false) { Write-Output NOT_RESPONDING; exit 0 }',
      'Write-Output UNKNOWN; exit 0'
    ].join('; ');
    const { stdout } = await execFileAsync('powershell.exe', ['-NoProfile', '-Command', script], { timeout: 5000 });
    const state = ((stdout || '').trim().split(/\r?\n/).map(s => s.trim()).filter(Boolean).pop() || 'UNKNOWN');

    if (state === 'RESPONDING') {
      return true;
    }

    if (state === 'NOT_RESPONDING') {
      logger?.warn({}, 'Rhino process is present but Windows reports it is not responding');
      return false;
    }

    // Treat unknown states as non-blocking so transient PowerShell probe issues
    // do not force unnecessary Rhino restarts.
    logger?.info({ state }, 'Rhino process response state is non-blocking');
    return true;
  } catch (err: any) {
    logger?.warn(
      { error: err?.message },
      'Failed to read Rhino responding state from Windows; treating as non-blocking'
    );
    return true;
  }
}

// Kill all Rhino processes at OS level
async function killRhinoProcess(logger?: PinoLogger): Promise<void> {
  try {
    if (process.platform === 'win32') {
      await execAsync('taskkill /F /T /IM Rhino.exe', { timeout: 10000 });
    } else {
      await execAsync('killall Rhino', { timeout: 10000 });
    }

    const exited = await waitForRhinoExit();
    if (exited) {
      logger?.info({}, 'Rhino process killed');
    } else {
      logger?.warn({}, 'Rhino kill command sent but process still appears to be running');
    }
  } catch (err: any) {
    logger?.info({ error: err?.message }, 'Kill Rhino result (may not exist)');
  }
}

/**
 * Verify Rhino is healthy by checking rhinocode target discovery and process responding state.
 * Uses `rhinocode list --json` instead of `_SelNone` so health does not depend on command output.
 */
export async function checkRhinoHealth(
  rhinoCodeCli: string,
  logger?: PinoLogger,
  options: { startupProbeGraceMs?: number } = {}
): Promise<boolean> {
  try {
    const targetCount = await getRhinoTargetCount(rhinoCodeCli, logger);

    if (targetCount <= 0) {
      const processExists = await rhinoProcessExists();
      if (processExists) {
        const processResponding = await rhinoProcessResponding(logger);
        if (!processResponding) {
          logger?.info({}, 'Rhino health check failed - process exists but is not responding');
          return false;
        }
      }

      logger?.info({ targetCount, processExists }, 'Rhino health check failed - no targetable Rhino instance');
      if (processExists) {
        logger?.warn(
          {},
          'Rhino process exists but RhinoCode cannot target it. A modal window (autosave/license/update) or script-server startup failure may be blocking automation.'
        );
      }
      return false;
    }

    // On Windows, reject GUI-hung Rhino even if target discovery succeeds.
    const responding = await rhinoProcessResponding(logger);
    if (!responding) {
      logger?.info({}, 'Rhino health check failed - process is not responding');
      return false;
    }

    if (isRhinoExecutionProbeEnabled()) {
      const startupProbeGraceMs = options.startupProbeGraceMs ?? 0;
      const probePassed = await checkRhinoExecutionProbe(rhinoCodeCli, startupProbeGraceMs, logger);
      if (!probePassed) {
        logger?.info({}, 'Rhino health check failed - execution probe did not produce marker file');
        return false;
      }
    }

    logger?.info({ targetCount }, 'Rhino health check passed');
    return true;
  } catch (err: any) {
    logger?.info({ error: err?.message }, 'Rhino health check failed - not responsive to commands');
    return false;
  }
}

/**
 * Check if Rhino is running and launch if needed.
 * Implements two-phase launch strategy:
 * - Phase 1: 45s polling for normal startup
 * - Phase 2: If failed, kill stuck process and retry with 60s timeout
 * 
 * @param rhinoCodeCli - Path to rhinocode binary
 * @param rhinoCli - Path to Rhino executable
 * @param logger - Optional logger for diagnostics
 * @returns Promise<boolean> - true if Rhino is running, false if failed to start
 */
export async function ensureRhinoRunning(
  rhinoCodeCli: string,
  rhinoCli: string,
  logger?: { info: (obj: any, msg?: string) => void; warn: (obj: any, msg?: string) => void }
): Promise<boolean> {
  const startupProbeGraceMs = getRhinoStartupProbeGraceMs();

  const checkHealthDuringStartup = async (): Promise<boolean> => {
    return checkRhinoHealth(rhinoCodeCli, logger, { startupProbeGraceMs });
  };
  const startupFailFastUnhealthyAttempts = getRhinoStartupFailFastUnhealthyAttempts();
  
  // Launch Rhino via OS-specific command
  const launchRhino = async (): Promise<void> => {
    logger?.info({ rhinoCli }, 'Launching Rhino');
    try {
      const startServerOnLaunch = shouldStartScriptServerOnRhinoLaunch();
      const startupMacro = getRhinoStartupMacro();

      if (process.platform === 'win32') {
        const rhinoArgs = ['/nosplash'];
        if (startServerOnLaunch) {
          rhinoArgs.push(`/runscript=${startupMacro}`);
        }

        logger?.info({ rhinoCli, rhinoArgs, startServerOnLaunch, startupMacro }, 'Launching Rhino with startup args');
        const child = spawn(rhinoCli, rhinoArgs, {
          detached: true,
          stdio: 'ignore',
          windowsHide: true,
        });
        child.on('error', (error) => {
          logger?.warn({ error: error.message }, 'Rhino launch process emitted error');
        });
        child.unref();
      } else {
        const openArgs = ['-a', rhinoCli, '--args', '-nosplash'];
        if (startServerOnLaunch) {
          openArgs.push(`-runscript=${startupMacro}`);
        }
        logger?.info({ rhinoCli, openArgs, startServerOnLaunch, startupMacro }, 'Launching Rhino with startup args');
        await execFileAsync('open', openArgs, { timeout: 5000 });
      }
    } catch (err: any) {
      logger?.warn({ error: err?.message }, 'Launch command completed (this may be normal)');
    }
  };

  // Poll for Rhino to become responsive to commands (uses full health check, not just list)
  const pollForRhino = async (maxAttempts: number, delayMs: number): Promise<boolean> => {
    let unhealthyWhileProcessExists = 0;

    for (let attempt = 1; attempt <= maxAttempts; attempt++) {
      await new Promise(resolve => setTimeout(resolve, delayMs));
      const responding = await checkRhinoHealth(rhinoCodeCli, logger, { startupProbeGraceMs });
      const processExists = await rhinoProcessExists();
      let processResponding: boolean | null = null;

      if (!responding && processExists) {
        processResponding = await rhinoProcessResponding(logger);
        if (!processResponding) {
          unhealthyWhileProcessExists += 1;
        } else {
          unhealthyWhileProcessExists = 0;
        }
      } else {
        unhealthyWhileProcessExists = 0;
      }

      logger?.info(
        {
          attempt,
          maxAttempts,
          responding,
          processExists,
          processResponding,
          unhealthyWhileProcessExists,
          startupProbeGraceMs,
          startupFailFastUnhealthyAttempts
        },
        'Polling for Rhino'
      );

      // Optional fail-fast for clearly hung Rhino windows. Disabled by default
      // (0) so cold startup jitter does not trigger needless kill/relaunch loops.
      if (
        startupFailFastUnhealthyAttempts > 0 &&
        !responding &&
        unhealthyWhileProcessExists >= startupFailFastUnhealthyAttempts
      ) {
        logger?.warn(
          { attempt, unhealthyWhileProcessExists, startupFailFastUnhealthyAttempts },
          'Rhino process appears hung during startup - failing early by configuration'
        );
        return false;
      }

      if (responding) {
        return true;
      }
    }
    return false;
  };

  // Main logic: Check if already running, then implement launch strategy
  logger?.info('Checking if Rhino is running');
  
  if (await checkHealthDuringStartup()) {
    logger?.info('Rhino is already running and responding');
    return true;
  }

  // If Rhino is unhealthy but a process exists, wait once and re-check before killing.
  // This keeps startup responsive while avoiding aggressive kill/relaunch churn.
  if (await rhinoProcessExists()) {
    logger?.warn({ startupProbeGraceMs }, 'Rhino process detected but not healthy yet - applying startup grace before pre-launch kill');
    await new Promise(resolve => setTimeout(resolve, startupProbeGraceMs));

    if (await checkHealthDuringStartup()) {
      logger?.info('Rhino became healthy during startup grace window');
      return true;
    }

    logger?.warn('Rhino process still unhealthy after startup grace - killing before launch');
    await killRhinoProcess(logger);
    await new Promise(resolve => setTimeout(resolve, 2000)); // Wait for cleanup
  }

  // Phase 1: Normal launch with 45s timeout
  logger?.info('Phase 1: Launching Rhino (45s polling)');
  await launchRhino();
  
  if (await pollForRhino(9, 5000)) { // 9 attempts x 5s = 45s
    logger?.info('Phase 1: Rhino started successfully');
    return true;
  }

  // Phase 1 failed - check if process is stuck
  logger?.warn('Phase 1 failed: Rhino did not respond within 45s');
  
  if (await rhinoProcessExists()) {
    logger?.warn('Rhino process detected but not responding - killing for recovery');
    await killRhinoProcess(logger);
    await new Promise(resolve => setTimeout(resolve, 2000)); // Wait for cleanup
  }

  // Phase 2: Recovery launch with 60s timeout
  logger?.info('Phase 2: Recovery launch (60s polling)');
  await launchRhino();
  
  if (await pollForRhino(12, 5000)) { // 12 attempts × 5s = 60s
    logger?.info('Phase 2: Rhino started successfully after recovery');
    return true;
  }

  logger?.warn('Phase 2 failed: Rhino did not start after recovery attempt');
  return false;
}

/**
 * Politely close Rhino: send `-_Exit N`, fall back to force-kill if needed.
 * Safe to call when Rhino isn't running (returns quickly). Used by:
 *   - pipeline end-of-job shutdown
 *   - processor poll loop when the keep-warm lease expires
 */
export async function closeRhino(
  rhinoCodeCli: string,
  logger?: PinoLogger
): Promise<void> {
  // Skip the exit command if no process is even running.
  if (!(await rhinoProcessExists())) {
    logger?.info({}, 'Rhino not running; nothing to close');
    return;
  }

  try {
    // -_Exit N skips the unsaved-changes prompt.
    await executeRhinoCommand(rhinoCodeCli, '-_Exit N', { timeout: 30_000 }, logger);
    logger?.info({}, 'Rhino exit command sent');
    await new Promise((r) => setTimeout(r, 2000));
    if (await rhinoProcessExists()) {
      logger?.warn({}, 'Rhino still running after Exit command - force killing');
      await killRhinoProcess(logger);
      await new Promise((r) => setTimeout(r, 1000));
      logger?.info({}, 'Rhino force killed');
    } else {
      logger?.info({}, 'Rhino exited cleanly');
    }
  } catch (exitErr: any) {
    logger?.warn({ error: exitErr?.message }, 'Rhino exit command failed - falling back to kill');
    try { await killRhinoProcess(logger); } catch {}
  }
}

// Re-exported so the processor can probe Rhino state without poking pipeline internals.
export async function isRhinoRunning(): Promise<boolean> {
  return rhinoProcessExists();
}

// ===========================
// Pipeline Types & Main Function
// ===========================

export interface PipelineInputs {
  id: string;
  algorithm: string;
  params: any;
  ghScriptsDir: string;
  outboxDir: string;
  baseName: string;
  inboxJsonPath: string;
  rhinoCli?: string;
  rhinoCodeCli?: string;
  bambuCli?: string;
  dryRun?: boolean;
  keepRhinoAlive?: boolean;  // If true, skip Rhino shutdown after job (dedicated license)
  // Optional structured logger and per-job log function
  logger?: { info: (obj: any, msg?: string) => void; warn: (obj: any, msg?: string) => void };
  jobLog?: (level: 'info' | 'warn', message: string, extra?: any) => void;
}

export interface PipelineOutputs {
  geometryPath: string;  // STL/3MF/OBJ path
  printPath?: string;    // 3MF with gcode (optional)
  sliceDurationSeconds?: number; // Bambu slicer wall-clock time
}

export async function runPipeline(input: PipelineInputs): Promise<PipelineOutputs> {
  // Use baseName from caller -- it matches the inbox JSON filename stem,
  // which the GH Python script uses as jobname for output files.
  const base = input.baseName;
  const geometryCandidates = [
    path.join(input.outboxDir, `${base}.stl`),
    path.join(input.outboxDir, `${base}.3mf`),
    path.join(input.outboxDir, `${base}.obj`),
  ];
  let geometryPath = geometryCandidates[0];
  const printPath = path.join(input.outboxDir, `${base}.gcode.3mf`);
  let pipelineError: Error | null = null;
  let shouldResetRhinoAfterFailure = false;

  const logInfo = (msg: string, extra?: any) => {
    input.logger?.info(extra || {}, msg);
    if (input.jobLog) input.jobLog('info', msg, extra);
  };
  const logWarn = (msg: string, extra?: any) => {
    input.logger?.warn(extra || {}, msg);
    if (input.jobLog) input.jobLog('warn', msg, extra);
  };

  const clearPipelineAttemptArtifacts = () => {
    const attemptArtifacts = [
      ...geometryCandidates,
      path.join(input.outboxDir, `${base}.meta.json`),
      path.join(input.outboxDir, 'log.txt')
    ];

    for (const artifact of attemptArtifacts) {
      try {
        if (fs.existsSync(artifact)) {
          fs.unlinkSync(artifact);
        }
      } catch (err: any) {
        logWarn('Failed to clear pipeline artifact before attempt', { artifact, error: err?.message });
      }
    }
  };

  if (input.dryRun) {
    // Produce tiny dummy files to exercise the flow
    fs.writeFileSync(geometryPath, 'solid dryrun\nendsolid dryrun\n');
    fs.writeFileSync(printPath, '3mf-dryrun');
    logInfo('DRY_RUN wrote placeholder files', { geometryPath, printPath });
    return { geometryPath, printPath };
  }

  // Resolve generator script path. Prefer {algorithm}.py (rhinocode script) over {algorithm}.gh
  // (GrasshopperPlayer): a runner .py bypasses Grasshopper entirely and keeps the algorithm as
  // plain, diffable Python. Falling back to .gh keeps every existing algorithm working unchanged.
  const pyScriptAbs = path.resolve(path.join(input.ghScriptsDir, `${input.algorithm}.py`));
  const ghScriptAbs = path.resolve(path.join(input.ghScriptsDir, `${input.algorithm}.gh`));
  let scriptKind: 'py' | 'gh';
  let scriptAbs: string;
  if (fs.existsSync(pyScriptAbs)) {
    scriptKind = 'py';
    scriptAbs = pyScriptAbs;
  } else if (fs.existsSync(ghScriptAbs)) {
    scriptKind = 'gh';
    scriptAbs = ghScriptAbs;
  } else {
    const errorMsg = `Generator script not found: neither ${pyScriptAbs} nor ${ghScriptAbs} exists. Ensure one exists under splint_geo_processor/generators/ or set GH_SCRIPTS_DIR.`;
    logWarn(errorMsg);
    throw new Error(errorMsg);
  }
  logInfo('Resolved generator script', { scriptKind, scriptAbs });

  // Rhino/Grasshopper step
  const rhinoCliPath = input.rhinoCli;
  if (!rhinoCliPath) {
    const errorMsg = 'RHINO_CLI not configured and DRY_RUN is false';
    logWarn(errorMsg);
    throw new Error(errorMsg);
  }
  const rhinoCodeCliPath = input.rhinoCodeCli;
  if (!rhinoCodeCliPath) {
    const errorMsg = 'RHINOCODE_CLI not configured and DRY_RUN is false';
    logWarn(errorMsg);
    throw new Error(errorMsg);
  }

  // Ensure Rhino is running using centralized function
  logInfo('Ensuring Rhino is running');
  const rhinoRunning = await ensureRhinoRunning(
    rhinoCodeCliPath,
    rhinoCliPath,
    input.logger
  );

  if (!rhinoRunning) {
    throw new Error('Rhino did not start successfully after launch attempts');
  }

  const runGrasshopperAttempt = async (attemptNumber: number): Promise<{ error: Error | null; lockLikely: boolean }> => {
    clearPipelineAttemptArtifacts();

    // Dispatch: .py runs directly via `rhinocode script`; .gh runs via GrasshopperPlayer.
    // Both paths write to the same log.txt / .meta.json so the poll loop below is identical.
    const execEnv = {
      ...process.env,
      SF_JOB_BASENAME: base,
      SF_OUTBOX_DIR: input.outboxDir,
      SF_INBOX_JSON: (input as any).inboxJsonPath || '',
      SF_PARAMS_JSON: typeof input.params === 'string' ? input.params : JSON.stringify(input.params ?? {})
    } as NodeJS.ProcessEnv;

    let ghStdout = '';
    let ghStderr = '';
    if (scriptKind === 'py') {
      const runCmd = `${rhinoCodeCliPath} script ${scriptAbs}`;
      logInfo('exec', { cmd: runCmd, attemptNumber });
      const res = await executeRhinoCodeCli(
        rhinoCodeCliPath,
        ['script', scriptAbs],
        { timeout: 10 * 60_000, env: execEnv },
        { info: logInfo, warn: logWarn }
      );
      ghStdout = res.stdout;
      ghStderr = res.stderr;
    } else {
      // rhinocode command "- _GrasshopperPlayer {gh_script_path}" (hyphen underscore)
      const ghArg = `-_GrasshopperPlayer "${scriptAbs}"`;
      const runCmd = `${rhinoCodeCliPath} command ${ghArg}`;
      logInfo('exec', { cmd: runCmd, attemptNumber });
      const res = await executeRhinoCommand(
        rhinoCodeCliPath,
        ghArg,
        { timeout: 10 * 60_000, env: execEnv },
        { info: logInfo, warn: logWarn }
      );
      ghStdout = res.stdout;
      ghStderr = res.stderr;
    }
    if (ghStdout && ghStdout.trim()) logInfo('stdout (rhinocode command)', { stdout: ghStdout.substring(0, 2000), attemptNumber });
    if (ghStderr && ghStderr.trim()) logWarn('stderr (rhinocode command)', { stderr: ghStderr.substring(0, 2000), attemptNumber });

    // Validate geometry output exists and is non-trivial, allowing time for file write.
    // Uses activity detection: if Grasshopper's log.txt is still growing, the timeout
    // resets so long-running multi-splint jobs aren't killed prematurely.
    // Watches for [PIPELINE_RESULT:...] signals in log.txt for fast completion detection.
    const start = Date.now();
    const inactivityTimeoutMs = Number(process.env.GH_INACTIVITY_TIMEOUT_MS || 90_000);
    const noProgressTimeoutMs = Number(process.env.GH_NO_PROGRESS_TIMEOUT_MS || 30_000);
    const maxTimeoutMs = Number(process.env.GH_MAX_TIMEOUT_MS || (10 * 60_000));
    let ok = false;
    let pipelineSignalFailure = false; // set true if log.txt contains [PIPELINE_RESULT:FAILURE]
    let size = 0;
    let lastSize = -1;
    let stableSizeCount = 0;
    let sawProgress = false;
    let timeoutReason: 'none' | 'no-progress' | 'inactivity' | 'max' = 'none';

    const findGeometryOutput = (): { path: string; size: number } | undefined => {
      for (const candidate of geometryCandidates) {
        if (!fs.existsSync(candidate)) continue;
        try {
          const stats = fs.statSync(candidate);
          if (stats.isFile()) {
            return { path: candidate, size: stats.size };
          }
        } catch {
          // Continue to next candidate
        }
      }
      return undefined;
    };

    // Activity detection via log.txt
    const ghLogPath = path.join(input.outboxDir, 'log.txt');
    const metaJsonPath = path.join(input.outboxDir, `${base}.meta.json`);
    let lastLogSize = 0;
    let lastActivityTime = Date.now();

    let lastProgressLog = 0;

    while (true) {
      const elapsed = Date.now() - start;
      const sinceActivity = Date.now() - lastActivityTime;

      // Hard ceiling: never wait longer than maxTimeoutMs
      if (elapsed >= maxTimeoutMs) {
        timeoutReason = 'max';
        break;
      }

      // If no progress has ever shown up, fail fast (locked Rhino signature)
      if (!sawProgress && elapsed >= noProgressTimeoutMs) {
        timeoutReason = 'no-progress';
        break;
      }

      // After some progress appears, allow longer inactivity timeout
      if (sawProgress && sinceActivity >= inactivityTimeoutMs) {
        timeoutReason = 'inactivity';
        break;
      }

      // Fast path: .meta.json means Python verified the STL and wrote metadata
      if (fs.existsSync(metaJsonPath)) {
        const found = findGeometryOutput();
        if (found && found.size >= 200) {
          geometryPath = found.path;
          size = found.size;
          sawProgress = true;
          logInfo('Detected .meta.json and geometry output', { geometryPath, size, attemptNumber });
          ok = true;
          break;
        }
      }

      // Fast path: check log.txt tail for pipeline result signals
      // These are written by splintcommon.confirm_job_is_processed_and_exit()
      try {
        if (fs.existsSync(ghLogPath)) {
          const currentLogSize = fs.statSync(ghLogPath).size;
          if (currentLogSize > lastLogSize) {
            // Read only the new bytes to check for signals
            const fd = fs.openSync(ghLogPath, 'r');
            const newBytes = Buffer.alloc(currentLogSize - lastLogSize);
            fs.readSync(fd, newBytes, 0, newBytes.length, lastLogSize);
            fs.closeSync(fd);
            const newContent = newBytes.toString('utf8');
            sawProgress = true;

            if (newContent.includes('[PIPELINE_RESULT:FAILURE]')) {
              logWarn('Detected [PIPELINE_RESULT:FAILURE] in log - aborting poll', { attemptNumber });
              pipelineSignalFailure = true;
              lastLogSize = currentLogSize;
              lastActivityTime = Date.now();
              break;
            }
            if (newContent.includes('[PIPELINE_RESULT:SUCCESS]')) {
              logInfo('Detected [PIPELINE_RESULT:SUCCESS] in log', { attemptNumber });
              // .meta.json should exist by now, but give a brief moment
              await new Promise(r => setTimeout(r, 200));
              const found = findGeometryOutput();
              if (found) {
                geometryPath = found.path;
                size = found.size;
              }
              ok = !!found && found.size >= 200;
              lastLogSize = currentLogSize;
              lastActivityTime = Date.now();
              if (ok) break;
            }

            lastLogSize = currentLogSize;
            lastActivityTime = Date.now();
          }
        }
      } catch {}

      // Fallback: output file size stability check (for older GH scripts)
      const found = findGeometryOutput();
      if (found) {
        if (geometryPath !== found.path) {
          geometryPath = found.path;
          lastSize = -1;
          stableSizeCount = 0;
        }
        if (found.size > size) {
          sawProgress = true;
          lastActivityTime = Date.now();
        }
        size = found.size;

        if (size >= 200) {
          if (size === lastSize) {
            stableSizeCount++;
            if (stableSizeCount >= 2) {
              ok = true;
              break;
            }
          } else {
            stableSizeCount = 0;
            lastSize = size;
          }
        }
      }

      // Periodic progress log every 5 seconds
      if (Date.now() - lastProgressLog >= 5_000) {
        lastProgressLog = Date.now();
        logInfo(
          `Waiting for output... elapsed=${Math.round(elapsed / 1000)}s, stlSize=${size}, logActivity=${Math.round(sinceActivity / 1000)}s ago`,
          { attemptNumber, sawProgress }
        );
      }

      await new Promise(r => setTimeout(r, 500));
    }

    // Read Grasshopper's log.txt and include in processing logs
    if (fs.existsSync(ghLogPath)) {
      try {
        const ghLogContent = fs.readFileSync(ghLogPath, 'utf-8');
        if (ghLogContent.trim()) {
          const formattedLog = '\n' +
            '================== RHINO LOG START ==================\n' +
            ghLogContent +
            '\n=================== RHINO LOG END ===================\n';
          logInfo(formattedLog.substring(0, 20000));
        }
      } catch (err: any) {
        logWarn('Failed to read Grasshopper log.txt', { error: err?.message, attemptNumber });
      }
    }

    if (pipelineSignalFailure) {
      return {
        error: new Error('Python pipeline reported failure via [PIPELINE_RESULT:FAILURE]'),
        lockLikely: false
      };
    }

    if (!ok) {
      const elapsed = Date.now() - start;
      const candidates = geometryCandidates.map((p) => path.basename(p)).join(', ');

      if (timeoutReason === 'no-progress') {
        const msg = `No Grasshopper progress detected after ${Math.round(elapsed / 1000)}s; treating Rhino as locked and eligible for restart. Tried: ${candidates}`;
        logWarn(msg, { attemptNumber, noProgressTimeoutMs, inactivityTimeoutMs });
        return { error: new Error(msg), lockLikely: true };
      }

      logWarn(
        `Geometry output missing or invalid (size=${size} bytes, waited=${Math.round(elapsed / 1000)}s, lastLogActivity=${Math.round((Date.now() - lastActivityTime) / 1000)}s ago, timeoutReason=${timeoutReason}). Tried: ${candidates}`,
        { attemptNumber, sawProgress }
      );
      return {
        error: new Error(`Geometry output missing or invalid after GrasshopperPlayer run (size=${size} bytes, waited=${Math.round(elapsed / 1000)}s, timeoutReason=${timeoutReason}). Tried: ${candidates}`),
        lockLikely: false
      };
    }

    logInfo(`Geometry output validated (${size} bytes)`, { geometryPath, waitTimeMs: Date.now() - start, attemptNumber });
    return { error: null, lockLikely: false };
  };

  const maxGrasshopperAttempts = 2;
  for (let attemptNumber = 1; attemptNumber <= maxGrasshopperAttempts; attemptNumber++) {
    const attemptResult = await runGrasshopperAttempt(attemptNumber);
    pipelineError = attemptResult.error;

    if (!pipelineError) {
      break;
    }

    if (!attemptResult.lockLikely || attemptNumber >= maxGrasshopperAttempts) {
      shouldResetRhinoAfterFailure = attemptResult.lockLikely;
      break;
    }

    shouldResetRhinoAfterFailure = true;
    logWarn('Detected likely Rhino locked state - forcing Rhino restart before retry', { attemptNumber });
    await killRhinoProcess(input.logger);
    await new Promise((r) => setTimeout(r, 2000));

    const restarted = await ensureRhinoRunning(rhinoCodeCliPath, rhinoCliPath, input.logger);
    if (!restarted) {
      pipelineError = new Error('Rhino restart failed after detecting likely locked state');
      break;
    }
  }

  // End-of-job Rhino shutdown.
  // Keep Rhino warm if (a) caller asked us to AND (b) we didn't just recover
  // from a locked-state failure (a fresh process is safer in that case).
  if (!input.keepRhinoAlive || (pipelineError && shouldResetRhinoAfterFailure)) {
    if (pipelineError && shouldResetRhinoAfterFailure) {
      logWarn('Resetting Rhino after locked-state failure (overriding keepRhinoAlive=true)');
    } else {
      logInfo('Closing Rhino (keepRhinoAlive=false)');
    }
    await closeRhino(rhinoCodeCliPath, input.logger);
  } else {
    logInfo('Keeping Rhino alive for next job (keepRhinoAlive=true)');
  }

  // If geometry validation failed, throw now (after Rhino cleanup)
  if (pipelineError) {
    throw pipelineError;
  }

  // Bambu Studio step (real CLI)
  let sliceDurationSeconds: number | undefined;
  if (input.bambuCli) {

    // Close the write/visibility race that intermittently caused Bambu to fail
    // with CLI_FILE_NOTFOUND on the large SizingRings 3mf: ensure the geometry
    // file is fully flushed and openable by a fresh handle before launching Bambu.
    const geometryReady = await waitForFileStableAndReadable(geometryPath, input.logger);
    if (!geometryReady) {
      throw new Error(`Geometry file not stable/readable before slicing: ${geometryPath}`);
    }

    const sliceStart = Date.now();

    const machineSettingsPath = path.join(__dirname, '../../printer-settings/machine/machine-final.json');
    const processSettingsPath = path.join(__dirname, '../../printer-settings/process/process-final.json');
    const filamentSettingsPath = path.join(__dirname, '../../printer-settings/filament/filament-final.json');
    const settingsJson = `${machineSettingsPath};${processSettingsPath}`;
    const filamentJson = `${filamentSettingsPath}`;

    const args = [
      '--orient', '1',
      '--arrange', '1',
      '--curr-bed-type', 'Textured PEI Plate',  // Must match string key in s_keys_map_BedType (PrintConfig.cpp line 723)
      '--load-settings', settingsJson,
      '--load-filaments', filamentJson,  // Single filament -> virtual slot 0 (runtime mapping via ams_mapping, see ../../agent-notes/ams_mapping_and_slicing.md)
      '--slice', '0',
      '--debug', '2',
      '--outputdir', input.outboxDir,  // Bambu writes result.json here (else CWD); makes per-job capture deterministic
      '--export-3mf', path.basename(printPath),  // Bambu joins outputdir + this; must be a bare filename or the path doubles
      geometryPath
    ];

    // Log full command
    const prettyArgs = args.map(a => (a.includes(' ') ? `"${a}"` : a)).join(' ');
    logInfo('execFile Bambu CLI: ', { cmd: `${input.bambuCli} ${prettyArgs}` });

    // Slicing is normally seconds; fail fast on hangs (e.g. UI dialogs blocking
    // the CLI like the update-available popup or GLFW/OpenGL init failures).
    const BAMBU_TIMEOUT_MS = 3 * 60_000;
    let bambuStdout = '';
    let bambuStderr = '';
    let bambuErr: any = null;
    try {
      const result = await execFileAsync(input.bambuCli, args, {
        timeout: BAMBU_TIMEOUT_MS,
        killSignal: 'SIGKILL', // SIGTERM is unreliable on Windows GUI hangs
        maxBuffer: 16 * 1024 * 1024,
      });
      bambuStdout = result.stdout ?? '';
      bambuStderr = result.stderr ?? '';
    } catch (err: any) {
      bambuErr = err;
      bambuStdout = err?.stdout ?? '';
      bambuStderr = err?.stderr ?? '';
    }
    sliceDurationSeconds = (Date.now() - sliceStart) / 1000;

    // Capture Bambu's own result.json (written to --outputdir) before the next job
    // overwrites it. It carries the authoritative return_code and error_string
    // (e.g. CLI_FILE_NOTFOUND=-3), which are far more useful than the opaque OS exit
    // code from execFile. Preserve a per-job copy so it survives and gets archived.
    let bambuResultCode: number | undefined;
    let bambuResultError: string | undefined;
    const resultJsonPath = path.join(input.outboxDir, 'result.json');
    try {
      if (fs.existsSync(resultJsonPath)) {
        const parsed = JSON.parse(fs.readFileSync(resultJsonPath, 'utf-8'));
        bambuResultCode = typeof parsed?.return_code === 'number' ? parsed.return_code : undefined;
        bambuResultError = typeof parsed?.error_string === 'string' ? parsed.error_string : undefined;
        fs.copyFileSync(resultJsonPath, path.join(input.outboxDir, `${base}.slice-result.json`));
        logInfo('Bambu result.json captured', { returnCode: bambuResultCode, errorString: bambuResultError });
      } else {
        logWarn('Bambu result.json not found after slice', { resultJsonPath });
      }
    } catch (err: any) {
      logWarn('Failed to read/parse Bambu result.json', { error: err?.message, resultJsonPath });
    }

    if (bambuStdout.trim()) logInfo('stdout (bambu)', { stdout: bambuStdout.substring(0, 2000) });
    if (bambuStderr.trim()) logWarn('stderr (bambu)', { stderr: bambuStderr.substring(0, 2000) });

    // On timeout, ensure no orphan bambu-studio.exe is left behind blocking the
    // next job's slot or filesystem locks.
    const timedOut = bambuErr?.killed === true || bambuErr?.signal === 'SIGKILL' || bambuErr?.signal === 'SIGTERM';
    if (timedOut) {
      logWarn(`Bambu CLI exceeded ${BAMBU_TIMEOUT_MS / 1000}s timeout - force killing any survivors`);
      try {
        if (process.platform === 'win32') {
          await execAsync('taskkill /F /T /IM bambu-studio.exe', { timeout: 10_000 });
        } else {
          await execAsync('pkill -9 -i bambu-studio', { timeout: 10_000 });
        }
      } catch { /* best-effort */ }
    }

    // Classify known hostile failure modes from stderr so the error surfaced to
    // the factory carries an actionable name, not just "Bambu failed".
    const haystack = `${bambuStdout}\n${bambuStderr}`;
    const knownFailures: Array<{ pattern: RegExp; label: string; hint?: string }> = [
      {
        pattern: /WGL: Failed to create OpenGL context|Failed to create GLFW window/i,
        label: 'OpenGL/GLFW init failed',
        hint: 'Bambu Studio could not create a window. Often caused by an interactive popup (e.g. "update available") that consumed the GL context. Try launching Bambu Studio interactively once to dismiss any pending dialog, then disable auto-update under Help > Settings.',
      },
      {
        pattern: /update.*available/i,
        label: 'Bambu update prompt blocked CLI',
        hint: 'Disable auto-update in Bambu Studio settings.',
      },
    ];
    const matchedFailures = knownFailures.filter(k => k.pattern.test(haystack));

    // Verify the print file actually landed. Bambu can exit 0 without producing
    // output when a hostile dialog is involved, so existence is the real gate.
    const printOk = fs.existsSync(printPath) && fs.statSync(printPath).size > 0;

    if (bambuErr || !printOk) {
      const reasons: string[] = [];
      if (timedOut) {
        reasons.push(`Bambu CLI timed out after ${BAMBU_TIMEOUT_MS / 1000}s`);
      } else if (bambuErr) {
        const code = bambuErr.code ?? bambuErr.exitCode;
        reasons.push(`Bambu CLI exited with error${code !== undefined ? ` (code=${code})` : ''}: ${bambuErr.message || 'unknown'}`);
      } else if (!printOk) {
        reasons.push('Bambu CLI exited cleanly but produced no print file');
      }
      // Surface Bambu's authoritative failure reason from result.json when present.
      if (bambuResultCode !== undefined || bambuResultError) {
        reasons.push(`Bambu result.json: return_code=${bambuResultCode ?? 'unknown'}${bambuResultError ? `, error_string="${bambuResultError}"` : ''}`);
      }
      for (const m of matchedFailures) {
        reasons.push(`Detected: ${m.label}${m.hint ? ' -- ' + m.hint : ''}`);
      }
      // Include a short stderr tail so the failure cause is in the result log.
      const stderrTail = bambuStderr.trim().split('\n').slice(-8).join('\n');
      if (stderrTail) reasons.push(`stderr tail:\n${stderrTail}`);

      const errorMsg = reasons.join('\n');
      logWarn('Bambu slicing failed', { durationSeconds: sliceDurationSeconds, timedOut, printOk });
      throw new Error(errorMsg);
    }

    logInfo(`Bambu slicer completed in ${sliceDurationSeconds.toFixed(1)}s`);
  }

  return { geometryPath, printPath: fs.existsSync(printPath) ? printPath : undefined, sliceDurationSeconds };
}
