import path from 'node:path';
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

function resolveEnvFile(explicitEnvFile?: string) {
  if (explicitEnvFile) {
    return explicitEnvFile;
  }

  // ENV_MODE from npm scripts, with platform fallback (Windows = production)
  const isProduction = process.env.ENV_MODE === 'production' || 
    (!process.env.ENV_MODE && process.platform === 'win32');
  return isProduction
    ? path.join(process.cwd(), '.env.production')
    : path.join(process.cwd(), '.env.local');
}

async function main() {
  const { args, envFile } = parseCliArgs(process.argv.slice(2));
  // Load env-specific settings first (committed), then .env secrets on top (gitignored)
  const resolvedEnvFile = resolveEnvFile(envFile);
  dotenv.config({ path: resolvedEnvFile });
  dotenv.config({ path: path.join(process.cwd(), '.env'), override: true });

  const [{ createLogger }, { Processor }, { loadConfig }] = await Promise.all([
    import('./utils/logger.js'),
    import('./processors/processor.js'),
    import('./config.js'),
  ]);

  const logger = createLogger();

  logger.info({ envFile: resolvedEnvFile, platform: process.platform }, 'splint_geo_processor starting...');
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
  if (args[0] && !args[0].startsWith('--')) {
    logger.info({ inspectId: args[0] }, 'Inspect mode: fetching job and launching Grasshopper');
    await processor.inspect(args[0]);
    return;
  }

  await processor.run();
}

// Main error handler with retry logic
main().catch(async (err) => {
  // Log to stderr for system logs
  console.error({
    error: err?.message,
    code: err?.code,
    stack: err?.stack,
    name: err?.name,
  });
  console.error('FATAL: Main process crashed unexpectedly');
  console.error('FATAL ERROR:', err);
  console.error('Process will exit with code 1');
  
  process.exit(1);
});
