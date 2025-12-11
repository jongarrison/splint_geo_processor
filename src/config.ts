import fs from 'node:fs';
import path from 'node:path';

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

function readSecretFile(fileName: string): string | undefined {
  try {
    const cwd = process.cwd();
    const filePath = path.join(cwd, 'secrets', fileName);
    if (fs.existsSync(filePath)) {
      return fs.readFileSync(filePath, 'utf8').trim();
    }
  } catch {}
  return undefined;
}

function readConfigJson(): Record<string, any> | undefined {
  try {
    const cwd = process.cwd();
    const filePath = path.join(cwd, 'secrets', 'config.json');
    if (fs.existsSync(filePath)) {
      const text = fs.readFileSync(filePath, 'utf8');
      return JSON.parse(text);
    }
  } catch {}
  return undefined;
}

export function loadConfig(): AppConfig {
  const json = readConfigJson() || {};
  const apiUrl = process.env.SF_API_URL
    || process.env.SPLINT_SERVER_URL
    || json.SF_API_URL
    || json.SPLINT_SERVER_URL
    || readSecretFile('api-url.txt')
    || readSecretFile('splint-server-url.txt')
    || 'http://localhost:3000';
  const apiKey = process.env.SF_API_KEY
    || process.env.SPLINT_SERVER_API_KEY
    || json.SF_API_KEY
    || json.SPLINT_SERVER_API_KEY
    || readSecretFile('api-key.txt')
    || readSecretFile('splint-server-key.txt')
    || '';
  const pollIntervalMs = Number(process.env.POLL_INTERVAL_MS || json.POLL_INTERVAL_MS || 3000);

  const home = process.env.HOME || process.env.USERPROFILE || '.';
  const baseDir = path.join(home, 'SplintFactoryFiles');
  const inboxDir = path.join(baseDir, 'inbox');
  const outboxDir = path.join(baseDir, 'outbox');
  const ghScriptsDir = process.env.GH_SCRIPTS_DIR || json.GH_SCRIPTS_DIR || path.join(process.cwd(), 'generators');
  const rhinoCli = process.env.RHINO_CLI || json.RHINO_CLI || readSecretFile('rhino-cli.txt');
  const rhinoCodeCli = process.env.RHINOCODE_CLI || json.RHINOCODE_CLI || readSecretFile('rhinocode-cli.txt');
  const bambuCli = process.env.BAMBU_CLI || json.BAMBU_CLI || readSecretFile('bambu-cli.txt');
  const dryRun = ((process.env.DRY_RUN || json.DRY_RUN) ?? '').toString().toLowerCase() === 'true' || json.DRY_RUN === true;

  // Ensure dirs
  fs.mkdirSync(inboxDir, { recursive: true });
  fs.mkdirSync(outboxDir, { recursive: true });
  fs.mkdirSync(ghScriptsDir, { recursive: true });

  if (!apiKey) {
    // We intentionally do not throw to allow starting without key; processor will warn and idle
    // This helps dev bootstrap without crashing.
  }

  // Determine environment from NODE_ENV, with fallback to URL detection
  let environment = process.env.NODE_ENV;
  if (!environment) {
    // Fallback: infer from API URL if NODE_ENV not set
    if (apiUrl.includes('localhost') || apiUrl.includes('127.0.0.1')) {
      environment = 'local';
    } else if (apiUrl.includes('splintfactory.com')) {
      environment = 'production';
    } else if (apiUrl.includes('vercel.app')) {
      environment = 'vercel';
    } else {
      environment = 'unknown';
    }
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
