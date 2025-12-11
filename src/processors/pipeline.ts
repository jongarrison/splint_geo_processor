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

  // 1) Ensure Rhino is running via rhinocode list --json
  const rhinoIsRunning = async (): Promise<boolean> => {
    const cmd = `${input.rhinoCodeCli} list --json`;
    try {
      const { stdout, stderr } = await execFileAsync(input.rhinoCodeCli!, ['list', '--json'], { timeout: 30_000 });
      // Log command and brief outputs
      if (stderr && stderr.trim()) {
        logWarn('stderr (rhinocode list)', { stderr: stderr.substring(0, 500) });
      }
      // Parse
      try {
        const parsed = JSON.parse(stdout.trim() || '[]');
        return Array.isArray(parsed) && parsed.length > 0;
      } catch {
        return false;
      }
    } catch (err: any) {
      logWarn('command failed (rhinocode list)', { cmd, error: err?.message || String(err) });
      return false;
    }
  };

  let running = false;
  try {
    running = await rhinoIsRunning();
  } catch {
    running = false;
  }

  if (!running) {
    // 2) Start Rhino in background (cross-platform)
    let openCmd: string;
    if (process.platform === 'win32') {
      // Windows: Use PowerShell Start-Process which can launch GUI apps from scheduled tasks
      openCmd = `powershell.exe -Command "Start-Process -FilePath '${input.rhinoCli}' -ArgumentList '/nosplash'"`;
    } else {
      // macOS: use open -a
      openCmd = `open -a "${input.rhinoCli}" --args -nosplash`;
    }
    
    // Log shell environment info
    logInfo('Shell environment', {
      SHELL: process.env.SHELL,
      COMSPEC: process.env.COMSPEC,
      PATH: process.env.PATH?.substring(0, 200),
      platform: process.platform,
      cwd: process.cwd()
    });
    
    logInfo('Launching Rhino', { cmd: openCmd, platform: process.platform });
    
    try {
      const { stdout: openStdout, stderr: openStderr } = await execAsync(openCmd, { timeout: 30_000 });
      if (openStdout && openStdout.trim()) logInfo('stdout (rhino launch)', { stdout: openStdout.substring(0, 500) });
      if (openStderr && openStderr.trim()) logWarn('stderr (rhino launch)', { stderr: openStderr.substring(0, 500) });
    } catch (err: any) {
      logWarn('Rhino launch command failed (may be ok if Rhino started)', { error: err?.message || String(err) });
    }
    
    // Wait and retry a few times
    logInfo('Waiting for Rhino to start...');
    for (let attempt = 0; attempt < 5; attempt++) {
      await new Promise((r) => setTimeout(r, 1000 * (attempt + 1)));
      try {
        running = await rhinoIsRunning();
        if (running) {
          logInfo('Rhino detected as running', { attempt: attempt + 1 });
          break;
        } else {
          logInfo('Rhino not detected yet', { attempt: attempt + 1 });
        }
      } catch (err: any) {
        logWarn('Error checking if Rhino is running', { attempt: attempt + 1, error: err?.message || String(err) });
      }
    }
    if (!running) {
      throw new Error('Rhino did not start successfully after 5 attempts');
    }
  } else {
    logInfo('Rhino already running');
  }

  // 3) Run GrasshopperPlayer with the script
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
  const { stdout: ghStdout, stderr: ghStderr } = await execFileAsync(input.rhinoCodeCli, ['command', ghArg], { timeout: 10 * 60_000, env: execEnv });
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
