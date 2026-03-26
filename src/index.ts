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

  // Check for inspect mode: npm run inspect -- <objectID or UUID>
  const inspectId = process.argv[2];
  if (inspectId) {
    logger.info({ inspectId }, 'Inspect mode: fetching job and launching Grasshopper');
    await processor.inspect(inspectId);
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
