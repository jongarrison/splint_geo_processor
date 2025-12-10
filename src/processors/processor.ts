import type pino from 'pino';
import axios, { AxiosInstance } from 'axios';
import fs from 'node:fs';
import path from 'node:path';
import { sleep } from '../utils/sleep.js';
import type { AppConfig } from '../config.js';
import { runPipeline } from './pipeline.js';
import FormData from 'form-data';

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
    const env = this.config.environment === 'production' ? 'prod' : 
                this.config.environment === 'local' ? 'local' : 
                this.config.environment;
    
    this.logger.info({ apiUrl: this.config.apiUrl }, `[${env}] Processor loop starting`);
    const intervalMs = this.config.pollIntervalMs || 5000;

    while (true) {
      try {
        // Poll for next job
        const resp = await this.http.get('/api/geometry-processing/next-job');

        if (resp.status === 404) {
          this.logger.info(`[${env}] No jobs available`);
          await sleep(intervalMs);
          continue;
        }
        if (resp.status === 401) {
          this.logger.warn(`[${env}] Unauthorized (401). Check SF_API_KEY or secrets/api-key.txt for a valid API key.`);
          await sleep(intervalMs);
          continue;
        }
        if (resp.status !== 200) {
          this.logger.warn({ status: resp.status, data: resp.data }, `[${env}] Unexpected response from next-job`);
          await sleep(intervalMs);
          continue;
        }

        const job = resp.data as any;
        this.logger.info({ 
          apiUrl: this.config.apiUrl, 
          jobId: job?.id ?? job?.ID ?? job?.Id 
        }, `[${env}] Job received from factory`);
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

          // Prepare files for multipart upload
          const geometryPath = outputs.geometryPath;
          const geometryName = path.basename(geometryPath);
          const geometrySize = fs.statSync(geometryPath).size;

          let printPath: string | undefined;
          let printName: string | undefined;
          let printSize: number | undefined;
          if (outputs.printPath && fs.existsSync(outputs.printPath)) {
            printPath = outputs.printPath;
            printName = path.basename(printPath);
            printSize = fs.statSync(printPath).size;
          }

          const processingLog = logLines.join('');
          
          // Log file sizes for debugging
          this.logger.info({
            geometryFileSize: geometrySize,
            geometryFileName: geometryName,
            printFileSize: printSize || 0,
            printFileName: printName || null,
            processingLogSize: processingLog.length,
          }, 'Uploading files via multipart');

          await this.reportSuccess(idPart, geometryPath, geometryName, printPath, printName, processingLog);
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
    if (processingLog) {
      // Truncate processing log to prevent 413 errors (server limit: 100KB, using 95KB for safety)
      const maxLogSize = 95 * 1024; // 95KB
      if (processingLog.length > maxLogSize) {
        const truncated = processingLog.slice(0, maxLogSize);
        payload.processingLog = truncated + '\n\n[Log truncated - original size: ' + processingLog.length + ' bytes]';
      } else {
        payload.processingLog = processingLog;
      }
    }
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

  private async reportSuccess(jobId: string, geometryPath: string, geometryName: string, printPath?: string, printName?: string, processingLog?: string) {
    // Upload files to blob storage first to avoid payload size limits
    try {
      // Step 1: Upload files to blob storage
      const uploadForm = new FormData();
      uploadForm.append('files', fs.createReadStream(geometryPath), {
        filename: geometryName,
        contentType: this.getContentType(geometryName),
      });
      
      if (printPath && printName) {
        uploadForm.append('files', fs.createReadStream(printPath), {
          filename: printName,
          contentType: this.getContentType(printName),
        });
      }
      
      const uploadResp = await this.http.post('/api/blob/upload', uploadForm, {
        headers: uploadForm.getHeaders(),
        maxBodyLength: Infinity,
        maxContentLength: Infinity,
      });
      
      if (uploadResp.status < 200 || uploadResp.status >= 300) {
        this.logger.error({ status: uploadResp.status, data: uploadResp.data }, 'Failed to upload files to blob storage');
        return;
      }
      
      const uploads = uploadResp.data.uploads;
      if (!uploads || uploads.length === 0) {
        this.logger.error('No uploads returned from blob storage');
        return;
      }
      
      // Find geometry and print file uploads
      const geometryUpload = uploads.find((u: any) => u.filename === geometryName);
      const printUpload = printPath && printName ? uploads.find((u: any) => u.filename === printName) : null;
      
      if (!geometryUpload) {
        this.logger.error('Geometry file upload not found in response');
        return;
      }
      
      // Step 2: Report result with blob URLs
      const payload: any = {
        GeometryProcessingQueueID: jobId,
        isSuccess: true,
        geometryBlobUrl: geometryUpload.url,
        geometryBlobPathname: geometryUpload.pathname,
        GeometryFileName: geometryName,
      };
      
      if (printUpload) {
        payload.printBlobUrl = printUpload.url;
        payload.printBlobPathname = printUpload.pathname;
        payload.PrintFileName = printName;
      }
      
      // Attach processing log (truncated if needed)
      if (processingLog) {
        const maxLogSize = 95 * 1024; // 95KB
        if (processingLog.length > maxLogSize) {
          const truncated = processingLog.slice(0, maxLogSize);
          payload.processingLog = truncated + '\n\n[Log truncated - original size: ' + processingLog.length + ' bytes]';
        } else {
          payload.processingLog = processingLog;
        }
      }
      
      const resp = await this.http.post('/api/geometry-processing/result', payload);
      if (resp.status >= 200 && resp.status < 300) {
        this.logger.info({ jobId }, 'Reported success with blob URLs');
      } else {
        this.logger.warn({ status: resp.status, data: resp.data }, 'Failed to report success');
      }
    } catch (err) {
      this.logger.error({ err }, 'Error reporting result');
    }
  }

  private getContentType(filename: string): string {
    const ext = path.extname(filename).toLowerCase();
    switch (ext) {
      case '.stl': return 'model/stl';
      case '.3mf': return 'model/3mf';
      case '.obj': return 'text/plain';
      case '.gcode': return 'text/plain';
      default: return 'application/octet-stream';
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
