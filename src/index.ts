import { createLogger } from './utils/logger.js';
import { Processor } from './processors/processor.js';
import { loadConfig } from './config.js';

const logger = createLogger();

async function main() {
  logger.info('splint_geo_processor starting...');
  const config = loadConfig();
  logger.info({ apiUrl: config.apiUrl, pollIntervalMs: config.pollIntervalMs }, 'Loaded config');
  const processor = new Processor(logger, config);
  await processor.run();
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
