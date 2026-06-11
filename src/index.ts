import path from 'node:path';
import fs from 'node:fs';
import dotenv from 'dotenv';

type CliArgs = {
  args: string[];
  envFile?: string;
};

function parseCliArgs(argv: string[]): CliArgs {
  const args: string[] = [];
  let envFile: string | undefined;

  for (let index = 0; index < argv.length; index += 1) {
    const arg = argv[index];

    if (arg.startsWith('--env-file=')) {
      envFile = arg.slice('--env-file='.length);
      continue;
    }

    if (arg === '--env-file' && argv[index + 1]) {
      envFile = argv[index + 1];
      index += 1;
      continue;
    }

    args.push(arg);
  }

  return { args, envFile };
}

function resolveTargetEnvFile(explicitEnvFile?: string): string {
  if (explicitEnvFile) {
    return explicitEnvFile;
  }

  // ENV_MODE=production => production API target; default is local
  const isProduction = process.env.ENV_MODE === 'production';
  return path.join(process.cwd(), isProduction ? '.env.target.production' : '.env.target.local');
}

function resolvePlatformEnvFile(): string {
  // Selects toolchain paths (Rhino, RhinoCode, Bambu) by OS
  return path.join(process.cwd(), process.platform === 'win32' ? '.env.platform.win' : '.env.platform.mac');
}

async function main() {
  const { args, envFile } = parseCliArgs(process.argv.slice(2));
  // Load target (API URL/intervals), then platform (toolchain paths), then .env secrets (wins all)
  const targetEnvFile = resolveTargetEnvFile(envFile);
  const platformEnvFile = resolvePlatformEnvFile();
  // Log ENV_MODE before loading dotenv so we can diagnose env inheritance issues
  const envMode = process.env.ENV_MODE ?? '(not set)';
  dotenv.config({ path: targetEnvFile });
  dotenv.config({ path: platformEnvFile });
  dotenv.config({ path: path.join(process.cwd(), '.env'), override: true });

  const [{ createLogger }, { Processor }, { loadConfig }] = await Promise.all([
    import('./utils/logger.js'),
    import('./processors/processor.js'),
    import('./config.js'),
  ]);

  const logger = createLogger();

  logger.info({ targetEnvFile, platformEnvFile, platform: process.platform, envMode }, 'splint_geo_processor starting...');
  const config = loadConfig();
  logger.info({ 
    environment: config.environment,
    apiUrl: config.apiUrl, 
    pollIntervalMs: config.pollIntervalMs 
  }, 'Loaded config');
  const processor = new Processor(logger, config);

  // CLI modes: --capture <id>, --test, or <id> (inspect mode)
  if (args[0] === '--capture' && args[1]) {
    logger.info({ jobId: args[1] }, 'Capture mode: fetching job and running pipeline');
    await processor.capture(args[1]);
    return;
  }

  if (args[0] === '--test') {
    const fixtureFilter = args[1] || undefined;
    logger.info({ fixtureFilter }, 'Test mode: running fixtures');
    await processor.testAll(fixtureFilter);
    return;
  }

  // Legacy inspect mode: bare objectId/UUID as first arg
  // Optional --save-fixture flag saves the job as a test fixture and runs the pipeline
  if (args[0] && !args[0].startsWith('--')) {
    const saveFixture = args.includes('--save-fixture');
    logger.info({ inspectId: args[0], saveFixture }, 'Inspect mode: fetching job and launching Grasshopper');
    await processor.inspect(args[0], saveFixture);
    return;
  }

  // Poll mode: self-restart loop so a crash doesn't leave the process dead
  while (true) {
    try {
      await processor.run();
      break; // run() is an infinite loop; only breaks on clean exit
    } catch (err: any) {
      logger.error({
        err: err?.message,
        code: err?.code,
      }, 'processor.run() crashed - restarting in 30 seconds');
      await new Promise(resolve => setTimeout(resolve, 30_000));
    }
  }
}

// Synchronously write crash details to a file before exit. pino's async transport
// may not flush in time for uncaughtException; this file is our reliable post-mortem.
function writeCrashLog(label: string, err: unknown): void {
  try {
    const home = process.env.HOME || process.env.USERPROFILE || '.';
    const logsDir = path.join(home, 'SplintFactoryFiles', 'logs');
    fs.mkdirSync(logsDir, { recursive: true });
    const e = err as any;
    const line = `${new Date().toISOString()} [${label}] ${e?.stack || e?.message || JSON.stringify(e)}\n`;
    fs.appendFileSync(path.join(logsDir, 'crashes.log'), line);
  } catch {
    // Last resort: stderr only
  }
}

// For truly unexpected errors that escape all try/catch blocks, exit cleanly so the
// wrapper script can restart the process. The self-restart loop in main() handles
// normal run() crashes; these handlers are last-resort for unknown bad states.
process.on('uncaughtException', (err) => {
  console.error('FATAL: Uncaught exception:', err?.message || err);
  writeCrashLog('uncaughtException', err);
  process.exit(1);
});

process.on('unhandledRejection', (reason) => {
  console.error('FATAL: Unhandled rejection:', reason);
  writeCrashLog('unhandledRejection', reason);
  process.exit(1);
});

// Startup errors (config, env loading) are still fatal
main().catch((err) => {
  console.error('FATAL: Startup failed:', err?.message || err);
  writeCrashLog('startup', err);
  process.exit(1);
});
