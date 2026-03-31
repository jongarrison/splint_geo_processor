import 'dotenv/config';
import { createLogger } from './utils/logger.js';
import { Processor } from './processors/processor.js';
import { loadConfig } from './config.js';

const logger = createLogger();

async function main() {
  logger.info('splint_geo_processor starting...');
  const config = loadConfig();
  logger.info({ 
    environment: config.environment,
    apiUrl: config.apiUrl, 
    pollIntervalMs: config.pollIntervalMs 
  }, 'Loaded config');
  const processor = new Processor(logger, config);

  // CLI modes: --capture <id>, --test, or <id> (inspect mode)
  const args = process.argv.slice(2);

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

  // Legacy inspect mode: bare objectID/UUID as first arg
  if (args[0] && !args[0].startsWith('--')) {
    logger.info({ inspectId: args[0] }, 'Inspect mode: fetching job and launching Grasshopper');
    await processor.inspect(args[0]);
    return;
  }

  await processor.run();
}

// Main error handler with retry logic
main().catch(async (err) => {
  logger.error({ 
    error: err?.message,
    code: err?.code,
    stack: err?.stack,
    name: err?.name
  }, 'FATAL: Main process crashed unexpectedly');
  
  // Log to stderr for system logs
  console.error('FATAL ERROR:', err);
  console.error('Process will exit with code 1');
  
  process.exit(1);
});
