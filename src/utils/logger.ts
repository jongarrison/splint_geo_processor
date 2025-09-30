import pino from 'pino';
import fs from 'node:fs';
import path from 'node:path';

export function createLogger() {
  // Log to file in ~/SplintFactoryFiles/logs and pretty-print to console
  const home = process.env.HOME || process.env.USERPROFILE || '.';
  const logsDir = path.join(home, 'SplintFactoryFiles', 'logs');
  fs.mkdirSync(logsDir, { recursive: true });

  const date = new Date();
  const ymd = `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`;
  const logFile = path.join(logsDir, `processor-${ymd}.log`);

  return pino({
    level: process.env.LOG_LEVEL || 'info',
    transport: {
      targets: [
        { target: 'pino/file', options: { destination: logFile, mkdir: true } },
        { target: 'pino-pretty', options: { translateTime: 'SYS:standard', colorize: true } },
      ]
    }
  });
}
