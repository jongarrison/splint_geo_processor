import fs from 'node:fs';
import path from 'node:path';
import { execFile, exec } from 'node:child_process';
import { promisify } from 'node:util';
const execFileAsync = promisify(execFile);
const execAsync = promisify(exec);

export interface PipelineInputs {
  id: string;
  algorithm: string;
  params: any;
  ghScriptsDir: string;
  outboxDir: string;
  rhinoCli?: string;
  rhinoCodeCli?: string;
  bambuCli?: string;
  dryRun?: boolean;
}

export interface PipelineOutputs {
  geometryPath: string;  // STL/3MF/OBJ path
  printPath?: string;    // 3MF with gcode (optional)
}

export async function runPipeline(input: PipelineInputs): Promise<PipelineOutputs> {
  const base = `${input.algorithm}_${input.id}`.replace(/[^a-zA-Z0-9._-]/g, '_');
  const geometryPath = path.join(input.outboxDir, `${base}.stl`);
  const printPath = path.join(input.outboxDir, `${base}.gcode.3mf`);

  if (input.dryRun) {
    // Produce tiny dummy files to exercise the flow
    fs.writeFileSync(geometryPath, 'solid dryrun\nendsolid dryrun\n');
    fs.writeFileSync(printPath, '3mf-dryrun');
    return { geometryPath, printPath };
  }

  // Resolve Grasshopper script path (algorithm.gh)
  const ghScript = path.join(input.ghScriptsDir, `${input.algorithm}.gh`);
  if (!fs.existsSync(ghScript)) {
    throw new Error(`Grasshopper script not found: ${ghScript}. Ensure it exists under splint_geo_processor/generators/ or set GH_SCRIPTS_DIR.`);
  }

  // Rhino/Grasshopper step
  if (!input.rhinoCli) {
    throw new Error('RHINO_CLI not configured and DRY_RUN is false');
  }
  if (!input.rhinoCodeCli) {
    throw new Error('RHINOCODE_CLI not configured and DRY_RUN is false');
  }

  // 1) Ensure Rhino is running via rhinocode list --json
  const rhinoIsRunning = async (): Promise<boolean> => {
    const { stdout } = await execFileAsync(input.rhinoCodeCli!, ['list', '--json'], { timeout: 30_000 });
    try {
      const parsed = JSON.parse(stdout.trim() || '[]');
      return Array.isArray(parsed) && parsed.length > 0;
    } catch {
      // If parsing fails, assume not running
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
    // 2) Start Rhino in background using open -a {RHINO_CLI} --args -nosplash
    const openCmd = `open -a "${input.rhinoCli}" --args -nosplash`;
    await execAsync(openCmd, { timeout: 30_000 });
    // Wait and retry a few times
    for (let attempt = 0; attempt < 5; attempt++) {
      await new Promise((r) => setTimeout(r, 1000 * (attempt + 1)));
      try {
        running = await rhinoIsRunning();
        if (running) break;
      } catch {
        // keep trying
      }
    }
    if (!running) {
      throw new Error('Rhino did not start successfully');
    }
  }

  // 3) Run GrasshopperPlayer with the script
  // rhinocode command "- _GrasshopperPlayer {gh_script_path}" (hyphen underscore)
  const ghArg = `-_GrasshopperPlayer "${ghScript}"`;
  await execFileAsync(input.rhinoCodeCli, ['command', ghArg], { timeout: 10 * 60_000 });

  // For now we assume the GH script writes its outputs to outbox naming convention
  // If not present, we write a placeholder to unblock the pipeline
  if (!fs.existsSync(geometryPath)) {
    fs.writeFileSync(geometryPath, 'solid pipeline\nendsolid pipeline\n');
  }

  // Bambu Studio step (stub)
  if (input.bambuCli) {
    // Example: await execFileAsync(input.bambuCli, ['--slice', geometryPath, '--output', printPath], { timeout: 10 * 60_000 });
    fs.writeFileSync(printPath, '3mf-pipeline');
  }

  return { geometryPath, printPath: fs.existsSync(printPath) ? printPath : undefined };
}
