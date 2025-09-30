import fs from 'node:fs';
import path from 'node:path';

export function getServerUrl(): string {
  return process.env.SPLINT_SERVER_URL || 'http://localhost:3000';
}

export function getApiKey(): string {
  const keyFromEnv = process.env.SPLINT_SERVER_API_KEY;
  if (keyFromEnv) return keyFromEnv.trim();

  const root = process.cwd();
  const keyPath = process.env.SPLINT_SERVER_KEY_FILE || path.join(root, 'secrets', 'splint-server-key.txt');
  try {
    const content = fs.readFileSync(keyPath, 'utf8');
    return content.trim();
  } catch (err) {
    throw new Error(`API key not found. Set SPLINT_SERVER_API_KEY or provide ${keyPath}`);
  }
}

export function getPaths() {
  const home = process.env.HOME || process.env.USERPROFILE || '.';
  const base = path.join(home, 'SplintFactoryFiles');
  const inbox = path.join(base, 'inbox');
  const outbox = path.join(base, 'outbox');
  const logs = path.join(base, 'logs');
  return { base, inbox, outbox, logs };
}
