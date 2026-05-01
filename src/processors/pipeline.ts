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
    const command = [
      'powershell.exe -NoProfile -Command',
      '"$p = Get-Process Rhino -ErrorAction SilentlyContinue | Select-Object -First 1;',
      'if ($null -eq $p) { Write-Output MISSING }',
      'elseif ($p.Responding) { Write-Output RESPONDING }',
      'else { Write-Output NOT_RESPONDING }"'
    ].join(' ');
    const { stdout } = await execAsync(command, { timeout: 5000 });
    const state = (stdout || '').trim();

    if (state === 'RESPONDING') {
      return true;
    }

    if (state === 'NOT_RESPONDING') {
      logger?.warn({}, 'Rhino process is present but Windows reports it is not responding');
      return false;
    }

    logger?.warn({ state }, 'Rhino process response state is unknown');
    return false;
  } catch (err: any) {
    logger?.warn({ error: err?.message }, 'Failed to read Rhino responding state from Windows');
    return false;
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
        // Keep non-Windows launch conservative until Rhino flag parity is verified.
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
    const startupProbeGraceMs = getRhinoStartupProbeGraceMs();

    for (let attempt = 1; attempt <= maxAttempts; attempt++) {
      await new Promise(resolve => setTimeout(resolve, delayMs));
      const responding = await checkRhinoHealth(rhinoCodeCli, logger, { startupProbeGraceMs });
      const processExists = await rhinoProcessExists();

      if (!responding && processExists) {
        unhealthyWhileProcessExists += 1;
      } else {
        unhealthyWhileProcessExists = 0;
      }

      logger?.info({ attempt, maxAttempts, responding, processExists, unhealthyWhileProcessExists, startupProbeGraceMs }, 'Polling for Rhino');

      // Fail fast if Rhino process appears to be launched but stays unhealthy.
      if (!responding && unhealthyWhileProcessExists >= 3) {
        logger?.warn({ attempt, unhealthyWhileProcessExists }, 'Rhino process exists but remains unhealthy after launch attempts - failing early');
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
  
  if (await checkRhinoHealth(rhinoCodeCli, logger)) {
    logger?.info('Rhino is already running and responding');
    return true;
  }

  // If Rhino is unhealthy but a process exists, kill it to ensure a clean launch
  if (await rhinoProcessExists()) {
    logger?.warn('Rhino process detected but not responding properly - killing before launch');
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

  // Resolve Grasshopper script path (algorithm.gh)
  const ghScript = path.join(input.ghScriptsDir, `${input.algorithm}.gh`);
  const ghScriptAbs = path.resolve(ghScript);
  if (!fs.existsSync(ghScriptAbs)) {
    const errorMsg = `Grasshopper script not found: ${ghScriptAbs}. Ensure it exists under splint_geo_processor/generators/ or set GH_SCRIPTS_DIR.`;
    logWarn(errorMsg);
    throw new Error(errorMsg);
  }

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

    // Run GrasshopperPlayer with the script
    // rhinocode command "- _GrasshopperPlayer {gh_script_path}" (hyphen underscore)
    const ghArg = `-_GrasshopperPlayer "${ghScriptAbs}"`;
    const runCmd = `${rhinoCodeCliPath} command ${ghArg}`;
    logInfo('exec', { cmd: runCmd, attemptNumber });
    const execEnv = {
      ...process.env,
      SF_JOB_BASENAME: base,
      SF_OUTBOX_DIR: input.outboxDir,
      SF_INBOX_JSON: (input as any).inboxJsonPath || '',
      SF_PARAMS_JSON: typeof input.params === 'string' ? input.params : JSON.stringify(input.params ?? {})
    } as NodeJS.ProcessEnv;

    const { stdout: ghStdout, stderr: ghStderr } = await executeRhinoCommand(
      rhinoCodeCliPath,
      ghArg,
      { timeout: 10 * 60_000, env: execEnv },
      { info: logInfo, warn: logWarn }
    );
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

  // Exit Rhino to free license unless keeping alive for next job
  if (!input.keepRhinoAlive || (pipelineError && shouldResetRhinoAfterFailure)) {
    const keepAliveDisabledByRecovery = pipelineError && shouldResetRhinoAfterFailure;
    if (keepAliveDisabledByRecovery) {
      logWarn('Resetting Rhino after locked-state failure (overriding keepRhinoAlive=true)');
    }
    logInfo('Closing Rhino (keepRhinoAlive=false)');
    try {
      // Use -_Exit N to exit without saving and without prompting
      await executeRhinoCommand(rhinoCodeCliPath, '-_Exit N', { timeout: 30_000 }, { info: logInfo, warn: logWarn });
      logInfo('Rhino exit command sent');
      await new Promise((r) => setTimeout(r, 2000));
      const stillRunning = await rhinoProcessExists();
      if (stillRunning) {
        logWarn('Rhino still running after Exit command - force killing');
        await killRhinoProcess(input.logger);
        await new Promise((r) => setTimeout(r, 1000));
        logInfo('Rhino force killed');
      } else {
        logInfo('Rhino exited cleanly');
      }
    } catch (exitErr: any) {
      logWarn('Rhino exit command failed', { error: exitErr?.message });
      try { await killRhinoProcess(input.logger); } catch {}
    }
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
      '--load-filaments', filamentJson,  // Single filament → virtual slot 0 (runtime mapping via ams_mapping, see ../../agent-notes/ams_mapping_and_slicing.md)
      '--slice', '0',
      '--debug', '2',
      '--export-3mf', printPath,
      geometryPath
    ];

    // Log full command
    const prettyArgs = args.map(a => (a.includes(' ') ? `"${a}"` : a)).join(' ');
    logInfo('execFile Bambu CLI: ', { cmd: `${input.bambuCli} ${prettyArgs}` });

    const { stdout: bambuStdout, stderr: bambuStderr } = await execFileAsync(input.bambuCli, args, { timeout: 10 * 60_000 });
    sliceDurationSeconds = (Date.now() - sliceStart) / 1000;
    if (bambuStdout && bambuStdout.trim()) logInfo('stdout (bambu)', { stdout: bambuStdout.substring(0, 2000) });
    if (bambuStderr && bambuStderr.trim()) logWarn('stderr (bambu)', { stderr: bambuStderr.substring(0, 2000) });
    logInfo(`Bambu slicer completed in ${sliceDurationSeconds.toFixed(1)}s`);
  }

  return { geometryPath, printPath: fs.existsSync(printPath) ? printPath : undefined, sliceDurationSeconds };
}
