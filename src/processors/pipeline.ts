import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { execFile, exec } from 'node:child_process';
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
    logger?.info({ stdout: stdout?.substring(0, 500), stderr: stderr?.substring(0, 500) }, 'rhinocode CLI completed');
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
  
  // Check if Rhino is responding via rhinocode CLI
  const isRhinoResponding = async (): Promise<boolean> => {
    try {
      const { stdout } = await executeRhinoCodeCli(rhinoCodeCli, ['list', '--json'], { timeout: 5000 }, logger);
      // Valid list output should be JSON array, empty [] if no Rhino running
      // Help text starts with "Usage:" when command fails
      const isHelpText = stdout.includes('Usage:') || stdout.includes('rhinocode [');
      if (isHelpText) {
        logger?.info({ stdout: stdout.substring(0, 200) }, 'rhinocode list returned help text - command may not exist');
        return false;
      }
      
      // Try to parse as JSON array
      try {
        const instances = JSON.parse(stdout.trim());
        const hasInstances = Array.isArray(instances) && instances.length > 0;
        logger?.info({ hasInstances, instanceCount: instances.length }, 'rhinocode list check');
        return hasInstances;
      } catch {
        // Not valid JSON, Rhino not responding
        logger?.info({ stdout: stdout.substring(0, 200) }, 'rhinocode list returned non-JSON');
        return false;
      }
    } catch (err: any) {
      logger?.info({ error: err?.message }, 'rhinocode list check failed - Rhino not responding');
      return false;
    }
  };

  // Check if Rhino process exists at OS level (independent of rhinocode)
  const rhinoProcessExists = async (): Promise<boolean> => {
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
  };

  // Kill all Rhino processes at OS level
  const killRhinoProcess = async (): Promise<void> => {
    try {
      if (process.platform === 'win32') {
        await execAsync('taskkill /F /IM Rhino.exe', { timeout: 10000 });
      } else {
        await execAsync('killall Rhino', { timeout: 10000 });
      }
      logger?.info('Rhino process killed');
    } catch (err: any) {
      logger?.info({ error: err?.message }, 'Kill Rhino result (may not exist)');
    }
  };

  // Launch Rhino via OS-specific command
  const launchRhino = async (): Promise<void> => {
    logger?.info({ rhinoCli }, 'Launching Rhino');
    try {
      if (process.platform === 'win32') {
        await execAsync(`powershell.exe -Command "Start-Process -FilePath '${rhinoCli}' -ArgumentList '/nosplash'"`, { 
          timeout: 5000 
        });
      } else {
        await execAsync(`open -a "${rhinoCli}" --args -nosplash`, { timeout: 5000 });
      }
    } catch (err: any) {
      logger?.warn({ error: err?.message }, 'Launch command completed (this may be normal)');
    }
  };

  // Poll for Rhino to become responsive
  const pollForRhino = async (maxAttempts: number, delayMs: number): Promise<boolean> => {
    for (let attempt = 1; attempt <= maxAttempts; attempt++) {
      await new Promise(resolve => setTimeout(resolve, delayMs));
      const responding = await isRhinoResponding();
      logger?.info({ attempt, maxAttempts, responding }, 'Polling for Rhino');
      if (responding) {
        return true;
      }
    }
    return false;
  };

  // Main logic: Check if already running, then implement two-phase launch
  logger?.info('Checking if Rhino is running');
  
  if (await isRhinoResponding()) {
    logger?.info('Rhino is already running and responding');
    return true;
  }

  // Phase 1: Normal launch with 45s timeout
  logger?.info('Phase 1: Launching Rhino (45s polling)');
  await launchRhino();
  
  if (await pollForRhino(9, 5000)) { // 9 attempts × 5s = 45s
    logger?.info('Phase 1: Rhino started successfully');
    return true;
  }

  // Phase 1 failed - check if process is stuck
  logger?.warn('Phase 1 failed: Rhino did not respond within 45s');
  
  if (await rhinoProcessExists()) {
    logger?.warn('Rhino process detected but not responding - killing for recovery');
    await killRhinoProcess();
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
  // Optional structured logger and per-job log function
  logger?: { info: (obj: any, msg?: string) => void; warn: (obj: any, msg?: string) => void };
  jobLog?: (level: 'info' | 'warn', message: string, extra?: any) => void;
}

export interface PipelineOutputs {
  geometryPath: string;  // STL/3MF/OBJ path
  printPath?: string;    // 3MF with gcode (optional)
}

export async function runPipeline(input: PipelineInputs): Promise<PipelineOutputs> {
  const base = `${input.algorithm}_${input.id}`.replace(/[^a-zA-Z0-9._-]/g, '_');
  const geometryPath = path.join(input.outboxDir, `${base}.stl`);
  const printPath = path.join(input.outboxDir, `${base}.gcode.3mf`);

  const logInfo = (msg: string, extra?: any) => {
    input.logger?.info(extra || {}, msg);
    if (input.jobLog) input.jobLog('info', msg, extra);
  };
  const logWarn = (msg: string, extra?: any) => {
    input.logger?.warn(extra || {}, msg);
    if (input.jobLog) input.jobLog('warn', msg, extra);
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
  if (!input.rhinoCli) {
    const errorMsg = 'RHINO_CLI not configured and DRY_RUN is false';
    logWarn(errorMsg);
    throw new Error(errorMsg);
  }
  if (!input.rhinoCodeCli) {
    const errorMsg = 'RHINOCODE_CLI not configured and DRY_RUN is false';
    logWarn(errorMsg);
    throw new Error(errorMsg);
  }

  // Ensure Rhino is running using centralized function
  logInfo('Ensuring Rhino is running');
  const rhinoRunning = await ensureRhinoRunning(
    input.rhinoCodeCli,
    input.rhinoCli,
    input.logger
  );

  if (!rhinoRunning) {
    throw new Error('Rhino did not start successfully after launch attempts');
  }

  // Run GrasshopperPlayer with the script
  // rhinocode command "- _GrasshopperPlayer {gh_script_path}" (hyphen underscore)
  const ghArg = `-_GrasshopperPlayer "${ghScriptAbs}"`;
  const runCmd = `${input.rhinoCodeCli} command ${ghArg}`;
  logInfo('exec', { cmd: runCmd });
  const execEnv = {
    ...process.env,
    SF_JOB_BASENAME: base,
    SF_OUTBOX_DIR: input.outboxDir,
    SF_INBOX_JSON: (input as any).inboxJsonPath || '',
    SF_PARAMS_JSON: typeof input.params === 'string' ? input.params : JSON.stringify(input.params ?? {})
  } as NodeJS.ProcessEnv;
  const { stdout: ghStdout, stderr: ghStderr } = await executeRhinoCommand(
    input.rhinoCodeCli,
    ghArg,
    { timeout: 10 * 60_000, env: execEnv },
    { info: logInfo, warn: logWarn }
  );
  if (ghStdout && ghStdout.trim()) logInfo('stdout (rhinocode command)', { stdout: ghStdout.substring(0, 2000) });
  if (ghStderr && ghStderr.trim()) logWarn('stderr (rhinocode command)', { stderr: ghStderr.substring(0, 2000) });

  // Validate geometry output exists and is non-trivial, allowing time for file write
  // Grasshopper may take time to flush large files to disk
  {
    const start = Date.now();
    const timeoutMs = 60000; // 60 second timeout for large files
    let ok = false;
    let size = 0;
    let lastSize = -1;
    let stableSizeCount = 0;
    
    while (Date.now() - start < timeoutMs) {
      if (fs.existsSync(geometryPath)) {
        try {
          const stats = fs.statSync(geometryPath);
          size = stats.size;
          
          // File must be at least 200 bytes and stable (not still growing)
          if (stats.isFile() && size >= 200) {
            // Check if size has stabilized (same size for 2 consecutive checks)
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
        } catch {}
      }
      await new Promise(r => setTimeout(r, 500));
    }

    // Check for Grasshopper's log.txt and include it in our logs
    const ghLogPath = path.join(input.outboxDir, 'log.txt');
    if (fs.existsSync(ghLogPath)) {
      try {
        const ghLogContent = fs.readFileSync(ghLogPath, 'utf-8');
        if (ghLogContent.trim()) {
          // Format the log with prominent markers and preserved line endings
          const formattedLog = '\n' +
            '================== RHINO LOG START ==================\n' +
            ghLogContent +
            '\n=================== RHINO LOG END ===================\n';
          logInfo(formattedLog.substring(0, 20000));
        }
      } catch (err: any) {
        logWarn('Failed to read Grasshopper log.txt', { error: err?.message });
      }
    }

    if (!ok) {
      logWarn(`Geometry output missing or invalid after GrasshopperPlayer run (size=${size} bytes, timeout=${timeoutMs}ms): ${geometryPath}`);
      throw new Error(`Geometry output missing or invalid after GrasshopperPlayer run (size=${size} bytes): ${geometryPath}`);
    }
    logInfo(`Geometry output validated (${size} bytes)`, { geometryPath, waitTimeMs: Date.now() - start });
  }

  // 4) Exit Rhino to free up license
  // Only exit if:
  //   - We launched Rhino (it wasn't already running)
  //   - AND we're in production (not local dev)
  const isLocalDev = process.env.NODE_ENV === 'local' || 
                     input.rhinoCli?.includes('RhinoWIP') ||
                     process.platform === 'darwin';
  
  if (!runningInitially && !isLocalDev) {
    logInfo('Closing Rhino to free license (production mode)');
    try {
      // Use -_Exit N to exit without saving and without prompting
      await executeRhinoCommand(input.rhinoCodeCli, '-_Exit N', { timeout: 30_000 }, { info: logInfo, warn: logWarn });
      logInfo('Rhino exit command sent');
      
      // Wait a moment for graceful exit
      await new Promise((r) => setTimeout(r, 2000));
      
      // Force kill any remaining Rhino processes to ensure cleanup
      // This handles cases where Exit command doesn't work due to GUI prompts
      const stillRunning = await rhinoProcessExists();
      if (stillRunning) {
        logWarn('Rhino still running after Exit command - force killing');
        await killRhinoProcess();
        await new Promise((r) => setTimeout(r, 1000));
        logInfo('Rhino force killed');
      } else {
        logInfo('Rhino exited cleanly');
      }
    } catch (exitErr: any) {
      logWarn('Rhino exit command failed', { error: exitErr?.message });
      // Still try to kill the process
      try {
        await killRhinoProcess();
      } catch {}
    }
  } else {
    const reason = runningInitially ? 'Rhino was already running' : 'local development mode';
    logInfo(`Keeping Rhino open (${reason})`);
  }

  // Bambu Studio step (real CLI)
  if (input.bambuCli) {

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
    if (bambuStdout && bambuStdout.trim()) logInfo('stdout (bambu)', { stdout: bambuStdout.substring(0, 2000) });
    if (bambuStderr && bambuStderr.trim()) logWarn('stderr (bambu)', { stderr: bambuStderr.substring(0, 2000) });
  }

  return { geometryPath, printPath: fs.existsSync(printPath) ? printPath : undefined };
}
