import type pino from 'pino';
import axios, { AxiosInstance } from 'axios';
import fs from 'node:fs';
import path from 'node:path';
import { sleep } from '../utils/sleep.js';
import type { AppConfig } from '../config.js';
import { runPipeline } from './pipeline.js';

export class Processor {
  private http: AxiosInstance;
  private inbox: string;
  private outbox: string;

  constructor(private logger: pino.Logger, private config: AppConfig) {
    this.http = axios.create({
      baseURL: this.config.apiUrl,
      timeout: 15000,
      headers: this.config.apiKey ? { Authorization: `Bearer ${this.config.apiKey}` } : {},
      validateStatus: () => true,
    });
    this.inbox = this.config.inboxDir;
    this.outbox = this.config.outboxDir;
    fs.mkdirSync(this.inbox, { recursive: true });
    fs.mkdirSync(this.outbox, { recursive: true });
  }

  async run() {
    this.logger.info({ apiUrl: this.config.apiUrl }, 'Processor loop starting');
    const intervalMs = this.config.pollIntervalMs || 5000;

    while (true) {
      try {
        // Poll for next job
        const resp = await this.http.get('/api/geometry-processing/next-job');

        if (resp.status === 404) {
          this.logger.info('No jobs available');
          await sleep(intervalMs);
          continue;
        }
        if (resp.status === 401) {
          this.logger.warn('Unauthorized (401). Check SF_API_KEY or secrets/api-key.txt for a valid API key.');
          await sleep(intervalMs);
          continue;
        }
        if (resp.status !== 200) {
          this.logger.warn({ status: resp.status, data: resp.data }, 'Unexpected response from next-job');
          await sleep(intervalMs);
          continue;
        }

        const job = resp.data as any;
        this.logger.info({ apiUrl: this.config.apiUrl, jobId: job?.id ?? job?.ID ?? job?.Id }, 'Successfully connected to API and received job');
        try {
          const keys = Object.keys(job || {});
          this.logger.debug({ keys }, 'next-job shape');
        } catch {}

  // Write input JSON to inbox. Filename: {GeometryAlgorithmName}_{GeometryProcessingQueueID}.json
  const idPart = job?.id ?? job?.ID ?? job?.Id ?? `ts_${Date.now()}`;
  const algoPart = job?.GeometryAlgorithmName || 'algorithm';
  const baseName = `${algoPart}_${idPart}`.replace(/[^a-zA-Z0-9._-]/g, '_');
  const inboxJson = path.join(this.inbox, `${baseName}.json`);
        const inputPayload = {
          id: idPart,
          algorithm: job?.GeometryAlgorithmName,
          params: job?.GeometryInputParameterData,
          metadata: {
            GeometryName: job?.GeometryName,
            CustomerNote: job?.CustomerNote,
            CustomerID: job?.CustomerID,
            objectID: job?.objectID
          }
        };
        fs.writeFileSync(inboxJson, JSON.stringify(inputPayload, null, 2), 'utf8');
        this.logger.info({ inboxJson, objectID: job?.objectID }, 'Wrote input JSON to inbox');

        // Prepare per-job archive directory and job-specific log file immediately
        const home = process.env.HOME || process.env.USERPROFILE || '.';
        const archiveRoot = path.join(home, 'SplintFactoryFiles', 'archive');
        const now = new Date();
        const pad2 = (n: number) => String(n).padStart(2, '0');
        const yy = pad2(now.getFullYear() % 100);
        const mm = pad2(now.getMonth() + 1);
        const dd = pad2(now.getDate());
        const HH = pad2(now.getHours());
        const MM = pad2(now.getMinutes());
        const archiveDirName = `${yy}${mm}${dd}-${HH}-${MM}-${baseName}`;
        const jobArchiveDir = path.join(archiveRoot, archiveDirName);
        fs.mkdirSync(jobArchiveDir, { recursive: true });
        const jobLogPath = path.join(jobArchiveDir, `${baseName}.log`);
        const jobLogStream = fs.createWriteStream(jobLogPath, { flags: 'a' });
        
        // Collect logs in memory to send to server (limit to 100KB)
        const logLines: string[] = [];
        const maxLogSize = 100 * 1024; // 100KB limit
        let currentLogSize = 0;
        
        const jobLog = (level: 'info'|'warn', message: string, extra?: any) => {
          const line = `${new Date().toISOString()} [${level}] ${message}${extra ? ' ' + JSON.stringify(extra) : ''}\n`;
          try { 
            jobLogStream.write(line); 
            // Add to in-memory log if under size limit
            if (currentLogSize + line.length <= maxLogSize) {
              logLines.push(line);
              currentLogSize += line.length;
            } else if (logLines.length > 0 && !logLines[logLines.length - 1].includes('[Log truncated]')) {
              // Add truncation notice once
              const truncMsg = '[Log truncated - exceeded 100KB limit]\n';
              logLines.push(truncMsg);
              currentLogSize += truncMsg.length;
            }
          } catch {}
        };

        // Single-threaded processing section: pause polling while we process this job
        try {
          this.logger.info({ id: idPart, algo: algoPart }, 'Starting geometry processing');
          const outputs = await runPipeline({
            id: String(idPart),
            algorithm: String(algoPart),
            params: inputPayload.params,
            ghScriptsDir: this.config.ghScriptsDir,
            outboxDir: this.outbox,
            baseName,
            inboxJsonPath: inboxJson,
            rhinoCli: this.config.rhinoCli,
            rhinoCodeCli: this.config.rhinoCodeCli,
            bambuCli: this.config.bambuCli,
            dryRun: this.config.dryRun,
            logger: this.logger,
            jobLog
          });

          // Read files and post success
          const fsRead = (p: string) => fs.readFileSync(p);
          const toB64 = (buf: Buffer) => buf.toString('base64');

          const geometryBuf = fsRead(outputs.geometryPath);
          const geometryB64 = toB64(geometryBuf);
          const geometryName = path.basename(outputs.geometryPath);

          let printB64: string | undefined;
          let printName: string | undefined;
          if (outputs.printPath && fs.existsSync(outputs.printPath)) {
            const printBuf = fsRead(outputs.printPath);
            printB64 = toB64(printBuf);
            printName = path.basename(outputs.printPath);
          }

          const processingLog = logLines.join('');
          await this.reportSuccess(idPart, geometryB64, geometryName, printB64, printName, processingLog);
        } catch (procErr: any) {
          this.logger.error({ err: procErr?.message }, 'Processing failed');
          // Log the error to jobLog so it appears in processing log sent to server
          jobLog('warn', `Processing failed: ${procErr?.message || 'Unknown error'}`);
          const processingLog = logLines.join('');
          await this.reportResult(idPart, false, String(procErr?.message || 'Processing failed'), processingLog);
        } finally {
          // Archive job files (inbox and any produced outbox files) regardless of success/failure
          try {
            const expectedGeometry = path.join(this.outbox, `${baseName}.stl`);
            const expectedPrint = path.join(this.outbox, `${baseName}.gcode.3mf`);
            await this.archiveJobFiles(archiveDirName, inboxJson, expectedGeometry, expectedPrint);
          } catch (archiveErr: any) {
            this.logger.warn({ err: archiveErr?.message }, 'Archiving job files failed');
          }
          try { jobLogStream.end(); } catch {}
          this.logger.info({ id: idPart }, 'Finished processing');
        }
      } catch (err) {
        // Check for connection refused errors and log them cleanly
        const isConnectionError = 
          (err as any)?.code === 'ECONNREFUSED' ||
          (err as any)?.cause?.code === 'ECONNREFUSED' ||
          (err as any)?.message?.includes('ECONNREFUSED');
        
        if (isConnectionError) {
          this.logger.warn('Server is down or unreachable (ECONNREFUSED)');
        } else {
          this.logger.error({ err }, 'Processor iteration failed');
        }
      }
      // throttle loop regardless of outcome
      await sleep(intervalMs);
    }
  }

  private async reportResult(jobId: string, isSuccess: boolean, errorMessage?: string, processingLog?: string) {
    const payload: any = {
      GeometryProcessingQueueID: jobId,
      isSuccess,
    };
    if (!isSuccess && errorMessage) payload.errorMessage = errorMessage;
    if (processingLog) payload.processingLog = processingLog;
    try {
      const resp = await this.http.post('/api/geometry-processing/result', payload);
      if (resp.status >= 200 && resp.status < 300) {
        this.logger.info({ jobId }, 'Reported result');
      } else {
        this.logger.warn({ status: resp.status, data: resp.data }, 'Failed to report result');
      }
    } catch (err) {
      this.logger.error({ err }, 'Error reporting result');
    }
  }

  private async reportSuccess(jobId: string, geometryB64: string, geometryName: string, printB64?: string, printName?: string, processingLog?: string) {
    const payload: any = {
      GeometryProcessingQueueID: jobId,
      isSuccess: true,
      GeometryFileContents: geometryB64,
      GeometryFileName: geometryName,
    };
    if (printB64 && printName) {
      payload.PrintFileContents = printB64;
      payload.PrintFileName = printName;
    }
    if (processingLog) {
      payload.processingLog = processingLog;
    }
    try {
      const resp = await this.http.post('/api/geometry-processing/result', payload);
      if (resp.status >= 200 && resp.status < 300) {
        this.logger.info({ jobId }, 'Reported success');
      } else {
        this.logger.warn({ status: resp.status, data: resp.data }, 'Failed to report success');
      }
    } catch (err) {
      this.logger.error({ err }, 'Error reporting success');
    }
  }

  private async archiveJobFiles(archiveDirName: string, inboxJson: string, geometryPath: string, printPath?: string) {
    const home = process.env.HOME || process.env.USERPROFILE || '.';
    const archiveRoot = path.join(home, 'SplintFactoryFiles', 'archive');
    const destDir = path.join(archiveRoot, archiveDirName);
    fs.mkdirSync(destDir, { recursive: true });

    const moveIfExists = (src: string | undefined, dstBaseName?: string) => {
      if (!src) return;
      if (!fs.existsSync(src)) return;
      const dest = path.join(destDir, dstBaseName || path.basename(src));
      try {
        fs.renameSync(src, dest);
      } catch {
        // fallback to copy+unlink if rename across devices fails
        try {
          fs.copyFileSync(src, dest);
          fs.unlinkSync(src);
        } catch (err) {
          // swallow; best-effort archiving
        }
      }
    };

    // Always archive the inbox JSON
    moveIfExists(inboxJson);

    // Archive all outbox artifacts for this job (move all files present)
    try {
      const outboxDir = this.outbox;
      const entries = fs.readdirSync(outboxDir, { withFileTypes: true });
      for (const ent of entries) {
        if (!ent.isFile()) continue;
        const src = path.join(outboxDir, ent.name);
        moveIfExists(src);
      }
    } catch (e) {
      // Best-effort; ignore listing failures
    }

    this.logger.info({ destDir }, 'Archived job files');
  }
}
