import fs from 'node:fs';
import path from 'node:path';
import pino from 'pino';

// Create logger that writes to the same log file as main logger
const home = process.env.HOME || process.env.USERPROFILE || '.';
const logsDir = path.join(home, 'SplintFactoryFiles', 'logs');
fs.mkdirSync(logsDir, { recursive: true });
const date = new Date();
const ymd = `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`;
const logFile = path.join(logsDir, `processor-${ymd}.log`);
const logger = pino(pino.destination(logFile));

export interface AppConfig {
  apiUrl: string;
  apiKey: string;
  pollIntervalMs: number;
  inboxDir: string;
  outboxDir: string;
  ghScriptsDir: string;        // Directory containing *.gh scripts
  rhinoCli?: string;           // Path to rhinocode or Rhino app CLI
  rhinoCodeCli?: string;       // Path to rhinocode CLI (for list/command)
  bambuCli?: string;           // Path to BambuStudio CLI
  dryRun?: boolean;            // If true, simulate outputs without invoking external tools
  environment: string;         // Environment name (local, production, or derived from URL)
}

// All config is loaded from env vars (.env -> .env.local/.env.production)
// See .env for common settings, .env.local/.env.production for overrides
export function loadConfig(): AppConfig {
  const apiUrl = process.env.SF_API_URL || 'http://localhost:3000';
  const apiKey = process.env.SF_API_KEY || '';

  logger.info({ apiUrl, hasApiKey: !!apiKey }, 'Config loaded from env');

  const pollIntervalMs = Number(process.env.POLL_INTERVAL_MS || 3000);

  const home = process.env.HOME || process.env.USERPROFILE || '.';
  const baseDir = path.join(home, 'SplintFactoryFiles');
  const inboxDir = path.join(baseDir, 'inbox');
  const outboxDir = path.join(baseDir, 'outbox');
  const ghScriptsDir = process.env.GH_SCRIPTS_DIR || path.join(process.cwd(), 'generators');
  const rhinoCli = process.env.RHINO_CLI;
  const rhinoCodeCli = process.env.RHINOCODE_CLI;
  const bambuCli = process.env.BAMBU_CLI;
  const dryRun = (process.env.DRY_RUN ?? '').toLowerCase() === 'true';

  // Ensure dirs
  fs.mkdirSync(inboxDir, { recursive: true });
  fs.mkdirSync(outboxDir, { recursive: true });
  fs.mkdirSync(ghScriptsDir, { recursive: true });

  // Determine environment from URL
  let environment: string;
  if (apiUrl.includes('localhost') || apiUrl.includes('127.0.0.1') || apiUrl.includes('.local')) {
    environment = 'local';
  } else if (apiUrl.includes('splintfactory.com')) {
    environment = 'production';
  } else if (apiUrl.includes('vercel.app')) {
    environment = 'vercel';
  } else {
    environment = 'unknown';
  }

  return { 
    apiUrl, 
    apiKey, 
    pollIntervalMs, 
    inboxDir, 
    outboxDir, 
    ghScriptsDir, 
    rhinoCli: rhinoCli || undefined, 
    rhinoCodeCli: rhinoCodeCli || undefined, 
    bambuCli: bambuCli || undefined, 
    dryRun,
    environment 
  };
}
