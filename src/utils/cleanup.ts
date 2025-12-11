import fs from 'node:fs';
import path from 'node:path';
import type pino from 'pino';

interface CleanupOptions {
  logsDir: string;
  archiveDir: string;
  daysToKeep: number;
}

/**
 * Clean up old log files and archived job artifacts
 */
export async function cleanupOldFiles(logger: pino.Logger, options: CleanupOptions): Promise<void> {
  const { logsDir, archiveDir, daysToKeep } = options;
  const cutoffTime = Date.now() - (daysToKeep * 24 * 60 * 60 * 1000);

  let deletedLogs = 0;
  let deletedArchives = 0;

  try {
    // Clean old log files
    if (fs.existsSync(logsDir)) {
      const logFiles = fs.readdirSync(logsDir);
      for (const file of logFiles) {
        if (!file.endsWith('.log')) continue;
        
        const filePath = path.join(logsDir, file);
        const stats = fs.statSync(filePath);
        
        if (stats.mtimeMs < cutoffTime) {
          fs.unlinkSync(filePath);
          deletedLogs++;
        }
      }
    }

    // Clean old archived job directories
    if (fs.existsSync(archiveDir)) {
      const archiveDirs = fs.readdirSync(archiveDir, { withFileTypes: true });
      for (const dir of archiveDirs) {
        if (!dir.isDirectory()) continue;
        
        const dirPath = path.join(archiveDir, dir.name);
        const stats = fs.statSync(dirPath);
        
        if (stats.mtimeMs < cutoffTime) {
          fs.rmSync(dirPath, { recursive: true, force: true });
          deletedArchives++;
        }
      }
    }

    if (deletedLogs > 0 || deletedArchives > 0) {
      logger.info(
        { deletedLogs, deletedArchives, daysToKeep },
        'Cleaned up old files'
      );
    }
  } catch (err) {
    logger.warn({ err }, 'Error during cleanup (non-fatal)');
  }
}
