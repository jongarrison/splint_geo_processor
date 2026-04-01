import type pino from 'pino';
import axios, { AxiosInstance } from 'axios';
import fs from 'node:fs';
import path from 'node:path';
import { sleep } from '../utils/sleep.js';
import type { AppConfig } from '../config.js';
import { runPipeline, executeRhinoCommand, ensureRhinoRunning } from './pipeline.js';
import { upload } from '@vercel/blob/client';
import FormData from 'form-data';
import { cleanupOldFiles } from '../utils/cleanup.js';

/** Result from running a single fixture through the pipeline */
interface FixtureResult {
  fixtureName: string;
  success: boolean;
  error?: string;
  durationSeconds: number;
  meta?: {
    mesh_count?: number;
    meshes?: Array<{ volume_mm3?: number; is_closed?: boolean; bbox_dimensions?: number[] }>;
    file_size_bytes?: number;
    elapsed_time_seconds?: number;
  };
  unionMethod?: string;
  sliced: boolean;
  printSizeBytes?: number;
  sliceDurationSeconds?: number;
  logSnippet?: string;
  timestamp: string;
}

/** Per-fixture entry in the test summary */
interface TestResultEntry {
  name: string;
  pass: boolean;
  issues: string[];
  volume?: number;
  volumePctDiff?: number;
  elapsed?: number;
  benchmarkElapsed?: number;
  unionMethod?: string;
  sliced?: boolean;
  printSizeBytes?: number;
  sliceDuration?: number;
  isFirstBenchmark?: boolean;
}

export class Processor {
  private http: AxiosInstance;
  private inbox: string;
  private outbox: string;
  private lastCleanupTime: number = 0;
  private readonly CLEANUP_INTERVAL_MS = 12 * 60 * 60 * 1000; // 12 hours (twice daily)
  private readonly DAYS_TO_KEEP = 7;

  constructor(private logger: pino.Logger, private config: AppConfig) {
    // Configure axios with appropriate timeouts and error handling
    this.http = axios.create({
      baseURL: this.config.apiUrl,
      timeout: 15000, // 15 second timeout for requests
      headers: this.config.apiKey ? { Authorization: `Bearer ${this.config.apiKey}` } : {},
      validateStatus: () => true, // Don't throw on HTTP errors, handle them manually
    });
    
    // Log axios configuration for debugging
    this.logger.info({ 
      baseURL: this.config.apiUrl,
      timeout: 15000,
      hasApiKey: !!this.config.apiKey
    }, 'HTTP client configured');
    
    this.inbox = this.config.inboxDir;
    this.outbox = this.config.outboxDir;
    fs.mkdirSync(this.inbox, { recursive: true });
    fs.mkdirSync(this.outbox, { recursive: true});
  }

  async run() {
    const env = this.config.environment === 'production' ? 'prod' : 
                this.config.environment === 'local' ? 'local' : 
                this.config.environment;
    
    this.logger.info({ apiUrl: this.config.apiUrl }, `[${env}] Processor loop starting`);
    
    // Validate API connectivity before entering main loop
    try {
      this.logger.info({ apiUrl: this.config.apiUrl }, `[${env}] Testing API connectivity...`);
      const testResp = await this.http.get('/api/geometry-processing/next-job');
      this.logger.info({ status: testResp.status }, `[${env}] API connectivity confirmed`);
    } catch (err: any) {
      this.logger.warn({ 
        error: err?.message,
        code: err?.code,
        apiUrl: this.config.apiUrl 
      }, `[${env}] API connectivity test failed - continuing anyway (will retry in loop)`);
    }
    
    // Run cleanup on startup
    await this.runCleanupIfNeeded(true);
    
    const intervalMs = this.config.pollIntervalMs || 5000;

    while (true) {
      try {
        // Poll for next job (priority: check for work first)
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
          // Truncate response body to avoid dumping entire HTML error pages into logs
          const preview = typeof resp.data === 'string' ? resp.data.slice(0, 200) : resp.data;
          this.logger.warn({ status: resp.status, data: preview }, `[${env}] Unexpected response from next-job`);
          await sleep(intervalMs);
          continue;
        }

        const job = resp.data as any;
        this.logger.info({ 
          apiUrl: this.config.apiUrl, 
          jobId: job?.id ?? job?.ID ?? job?.Id,
        }, `[${env}] Job received from factory`);
        
        try {
          const markStartedResp = await this.http.post('/api/geometry-processing/mark-started', {
            jobId: job?.id
          });
          if (markStartedResp.status === 200) {
            this.logger.info({ jobId: job?.id }, 'Marked job as started');
          } else {
            this.logger.warn({ 
              jobId: job?.id, 
              status: markStartedResp.status 
            }, 'Failed to mark job as started (continuing anyway)');
          }
        } catch (markErr: any) {
          this.logger.warn({ 
            jobId: job?.id, 
            error: markErr?.message 
          }, 'Error marking job as started (continuing anyway)');
        }
        
        try {
          const keys = Object.keys(job || {});
          this.logger.debug({ keys }, 'next-job shape');
        } catch {}

  // Write input JSON to inbox. Filename: {GeometryAlgorithmName}_{GeometryProcessingQueueID}.json
  const idPart = job?.id ?? job?.ID ?? job?.Id ?? `ts_${Date.now()}`;
  const algoPart = job?.GeometryAlgorithmName || 'algorithm';
  
  // Clean out all files in the inbox before writing new input
  try {
    const existingFiles = fs.readdirSync(this.inbox);
    if (existingFiles.length > 0) {
      this.logger.info({ count: existingFiles.length, files: existingFiles }, 'Cleaning inbox before new job');
      for (const file of existingFiles) {
        fs.unlinkSync(path.join(this.inbox, file));
      }
    }
  } catch (err: any) {
    this.logger.warn({ error: err?.message }, 'Failed to clean up inbox files (continuing)');
  }

  // Clean out all files in the outbox before new job
  try {
    const existingFiles = fs.readdirSync(this.outbox);
    if (existingFiles.length > 0) {
      this.logger.info({ count: existingFiles.length, files: existingFiles }, 'Cleaning outbox before new job');
      for (const file of existingFiles) {
        fs.unlinkSync(path.join(this.outbox, file));
      }
    }
  } catch (err: any) {
    this.logger.warn({ error: err?.message }, 'Failed to clean up outbox files (continuing)');
  }
  
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
          
          // Read mesh metadata if generated by Grasshopper/Python
          let meshMetadata: string | undefined;
          const metaPath = path.join(this.outbox, `${baseName}.meta.json`);
          if (fs.existsSync(metaPath)) {
            try {
              meshMetadata = fs.readFileSync(metaPath, 'utf-8');
              this.logger.info({ metaPath }, 'Read mesh metadata');
            } catch (metaErr: any) {
              this.logger.warn({ error: metaErr?.message }, 'Failed to read mesh metadata');
            }
          }
          
          // Log file sizes for debugging
          this.logger.info({
            geometryFileSize: geometrySize,
            geometryFileName: geometryName,
            printFileSize: printSize || 0,
            printFileName: printName || null,
            processingLogSize: processingLog.length,
            hasMeshMetadata: !!meshMetadata,
          }, 'Uploading files via multipart');

          // Wrap reportSuccess in try-catch to prevent network errors from killing the process
          try {
            await this.reportSuccess(idPart, geometryPath, geometryName, printPath, printName, processingLog, meshMetadata);
          } catch (reportErr: any) {
            this.logger.error({ 
              error: reportErr?.message,
              code: reportErr?.code,
              stack: reportErr?.stack 
            }, 'Failed to report success - job completed but upload failed');
          }
        } catch (procErr: any) {
          this.logger.error({ err: procErr?.message }, 'Processing failed');
          // Log the error to jobLog so it appears in processing log sent to server
          jobLog('warn', `Processing failed: ${procErr?.message || 'Unknown error'}`);
          const processingLog = logLines.join('');
          // Wrap reportResult in try-catch to prevent network errors from killing the process
          try {
            await this.reportResult(idPart, false, String(procErr?.message || 'Processing failed'), processingLog);
          } catch (reportErr: any) {
            this.logger.error({ 
              error: reportErr?.message,
              code: reportErr?.code,
              stack: reportErr?.stack 
            }, 'Failed to report failure - processing failed AND reporting failed');
          }
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
      } catch (err: any) {
        // Enhanced error handling with detailed logging for better debugging
        const errorCode = err?.code || err?.response?.status || 'UNKNOWN';
        const errorName = err?.name || 'Error';
        const errorMessage = err?.message || 'Unknown error';
        
        // Check for various network/connection errors
        const isConnectionRefused = 
          err?.code === 'ECONNREFUSED' ||
          err?.cause?.code === 'ECONNREFUSED' ||
          errorMessage.includes('ECONNREFUSED');
        
        const isTimeout = 
          err?.code === 'ETIMEDOUT' ||
          err?.code === 'ECONNABORTED' ||
          errorName === 'AxiosError' && errorMessage.includes('timeout');
        
        const isNetworkError = 
          err?.code === 'ENOTFOUND' ||
          err?.code === 'ENETUNREACH' ||
          err?.code === 'EAI_AGAIN';
        
        // Log appropriate message based on error type
        if (isConnectionRefused) {
          this.logger.warn({ errorCode, errorMessage }, 'Server is down or unreachable (ECONNREFUSED) - will retry');
        } else if (isTimeout) {
          this.logger.warn({ 
            errorCode, 
            errorName,
            timeout: err?.config?.timeout || 'unknown',
            url: err?.config?.url || 'unknown'
          }, 'Request timeout - will retry');
        } else if (isNetworkError) {
          this.logger.warn({ errorCode, errorMessage }, 'Network error - will retry');
        } else {
          // Log full error details for unexpected errors
          this.logger.error({ 
            errorCode,
            errorName,
            errorMessage,
            errorStack: err?.stack,
            url: err?.config?.url,
            method: err?.config?.method
          }, 'Processor iteration failed - will retry');
        }
      }
      
      // Run periodic cleanup (once or twice daily)
      await this.runCleanupIfNeeded();
      
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
    } catch (err: any) {
      // Re-throw to allow caller to handle (e.g., suppress 404 for debug jobs)
      if (err?.response?.status) {
        this.logger.warn({ status: err.response.status, data: err.response.data }, 'Failed to report result');
        throw err;
      }
      this.logger.error({ err }, 'Error reporting result');
      throw err;
    }
  }

  private async reportSuccess(jobId: string, geometryPath: string, geometryName: string, printPath?: string, printName?: string, processingLog?: string, meshMetadata?: string) {
    // Upload files to blob storage
    // Local dev uses multipart upload, production uses Vercel Blob client upload
    try {
      this.logger.info({ geometryName, printName, environment: this.config.environment }, 'Uploading files to blob storage');

      const isLocalDev = this.config.environment === 'local';

      if (isLocalDev) {
        // Local development: Use multipart upload (legacy format for filesystem storage)
        await this.reportSuccessMultipart(jobId, geometryPath, geometryName, printPath, printName, processingLog, meshMetadata);
      } else {
        // Production: Use Vercel Blob client upload
        await this.reportSuccessClientUpload(jobId, geometryPath, geometryName, printPath, printName, processingLog, meshMetadata);
      }
    } catch (err) {
      this.logger.error({ err }, 'Error reporting result');
    }
  }

  private async reportSuccessClientUpload(jobId: string, geometryPath: string, geometryName: string, printPath?: string, printName?: string, processingLog?: string, meshMetadata?: string) {
    // Production: Upload directly to Vercel Blob using client upload pattern
    this.logger.info({ environment: this.config.environment }, 'Using Vercel Blob client upload (production mode)');

    // Step 1: Upload geometry file
    const geometryBuffer = fs.readFileSync(geometryPath);
    const geometryBlob = new Blob([geometryBuffer], { 
      type: this.getContentType(geometryName) 
    });
    
    const geometryUpload = await upload(geometryName, geometryBlob, {
      access: 'public',
      handleUploadUrl: `${this.config.apiUrl}/api/blob/upload`,
      headers: this.config.apiKey ? { 
        Authorization: `Bearer ${this.config.apiKey}` 
      } : {},
    });

    this.logger.info({ 
      pathname: geometryUpload.pathname, 
      url: geometryUpload.url,
      size: geometryBuffer.length
    }, 'Geometry file uploaded');

    // Step 2: Upload print file if present
    let printUpload;
    if (printPath && printName) {
      const printBuffer = fs.readFileSync(printPath);
      const printBlob = new Blob([printBuffer], { 
        type: this.getContentType(printName) 
      });
      
      printUpload = await upload(printName, printBlob, {
        access: 'public',
        handleUploadUrl: `${this.config.apiUrl}/api/blob/upload`,
        headers: this.config.apiKey ? { 
          Authorization: `Bearer ${this.config.apiKey}` 
        } : {},
      });

      this.logger.info({ 
        pathname: printUpload.pathname, 
        url: printUpload.url,
        size: printBuffer.length
      }, 'Print file uploaded');
    }

    // Step 3: Report result with blob URLs
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
    
    if (meshMetadata) {
      payload.meshMetadata = meshMetadata;
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
  }

  private async reportSuccessMultipart(jobId: string, geometryPath: string, geometryName: string, printPath?: string, printName?: string, processingLog?: string, meshMetadata?: string) {
    // Local development: Use multipart upload to filesystem storage
    this.logger.info({ environment: this.config.environment }, 'Using multipart upload to filesystem (local development mode)');

    // Step 1: Upload files via multipart form
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
      this.logger.error({ status: uploadResp.status, data: uploadResp.data }, 'Failed to upload files');
      return;
    }
    
    const uploads = uploadResp.data.uploads;
    if (!uploads || uploads.length === 0) {
      this.logger.error('No uploads returned');
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
    
    if (meshMetadata) {
      payload.meshMetadata = meshMetadata;
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

  private async runCleanupIfNeeded(force: boolean = false): Promise<void> {
    const now = Date.now();
    const timeSinceLastCleanup = now - this.lastCleanupTime;
    
    if (!force && timeSinceLastCleanup < this.CLEANUP_INTERVAL_MS) {
      return; // Not time yet
    }
    
    const home = process.env.HOME || process.env.USERPROFILE || '.';
    const baseDir = path.join(home, 'SplintFactoryFiles');
    const logsDir = path.join(baseDir, 'logs');
    const archiveDir = path.join(baseDir, 'archive');
    
    await cleanupOldFiles(this.logger, {
      logsDir,
      archiveDir,
      daysToKeep: this.DAYS_TO_KEEP,
    });
    
    this.lastCleanupTime = now;
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

  /**
   * Inspect mode: fetch a job by objectID or UUID from the server,
   * write its data to the inbox, launch Rhino/Grasshopper, and exit.
   * Read-only -- no server mutations.
   */
  async inspect(jobIdentifier: string) {
    this.logger.info({ jobIdentifier }, 'Inspect: fetching job from server');

    const resp = await this.http.get(`/api/geometry-processing/job-by-id/${encodeURIComponent(jobIdentifier)}`);

    if (resp.status === 404) {
      this.logger.error({ jobIdentifier }, 'Inspect: job not found (checked both UUID and objectID)');
      process.exit(1);
    }
    if (resp.status !== 200) {
      this.logger.error({ status: resp.status, data: resp.data }, 'Inspect: failed to fetch job');
      process.exit(1);
    }

    const job = resp.data as any;
    this.logger.info({
      id: job.id,
      objectID: job.objectID,
      algorithm: job.GeometryAlgorithmName,
      isProcessSuccessful: job.isProcessSuccessful,
      processCompleted: !!job.ProcessCompletedTime,
    }, 'Inspect: job loaded');

    // Reuse the debug launch flow: write inbox JSON, launch Rhino, open GH script
    const algoPart = job.GeometryAlgorithmName || 'algorithm';
    const idPart = job.id || `inspect_${Date.now()}`;
    const baseName = `${algoPart}_${idPart}`.replace(/[^a-zA-Z0-9._-]/g, '_');

    // Clean stale inbox files for this algorithm
    try {
      const existingFiles = fs.readdirSync(this.inbox);
      const algoPrefix = `${algoPart}_`;
      const filesToClean = existingFiles.filter(f => f.startsWith(algoPrefix) && f.endsWith('.json'));
      for (const file of filesToClean) {
        fs.unlinkSync(path.join(this.inbox, file));
      }
      if (filesToClean.length > 0) {
        this.logger.info({ count: filesToClean.length }, 'Inspect: cleaned stale inbox files');
      }
    } catch (err: any) {
      this.logger.warn({ error: err?.message }, 'Inspect: inbox cleanup failed (continuing)');
    }

    // Write input JSON to inbox (same shape the GH scripts expect)
    const inboxJson = path.join(this.inbox, `${baseName}.json`);
    const inputPayload = {
      id: idPart,
      algorithm: algoPart,
      params: job.GeometryInputParameterData,
      metadata: {
        GeometryName: job.GeometryName,
        CustomerNote: job.JobNote,
        objectID: job.objectID,
      }
    };
    fs.writeFileSync(inboxJson, JSON.stringify(inputPayload, null, 2), 'utf8');
    this.logger.info({ inboxJson, objectID: job.objectID }, 'Inspect: wrote input JSON to inbox');

    // Offer to save as test fixture
    const objectID = job.objectID || jobIdentifier;
    const fixtureName = `${algoPart}_${objectID}`.replace(/[^a-zA-Z0-9._-]/g, '_');
    const fixturesDir = path.resolve('test-fixtures');
    const fixturePath = path.join(fixturesDir, `${fixtureName}.fixture.json`);

    if (fs.existsSync(fixturePath)) {
      this.logger.info({ fixturePath }, 'Inspect: fixture already exists');
    } else {
      // Flush pino transports before prompting (they run in worker threads)
      await new Promise(resolve => setTimeout(resolve, 500));
      process.stderr.write('\nSave as test fixture? (y/n) ');
      const answer = await new Promise<string>(resolve => {
        process.stdin.setEncoding('utf8');
        process.stdin.resume();
        process.stdin.once('data', (data) => {
          process.stdin.pause();
          resolve(data.toString().trim());
        });
      });
      if (answer.toLowerCase() === 'y') {
        fs.mkdirSync(fixturesDir, { recursive: true });
        fs.writeFileSync(fixturePath, JSON.stringify(inputPayload, null, 2), 'utf8');
        this.logger.info({ fixturePath }, 'Inspect: saved test fixture');

        // Run pipeline to capture benchmark
        this.logger.info('Inspect: running pipeline to capture benchmark...');
        const result = await this.runFixture(fixtureName, inputPayload);
        const resultPath = path.join(fixturesDir, `${fixtureName}.result.json`);
        fs.writeFileSync(resultPath, JSON.stringify(result, null, 2), 'utf8');

        const benchmarkPath = path.join(fixturesDir, `${fixtureName}.benchmark.json`);
        if (result.success && !fs.existsSync(benchmarkPath)) {
          fs.writeFileSync(benchmarkPath, JSON.stringify(result, null, 2), 'utf8');
          this.logger.info({ benchmarkPath }, 'Inspect: wrote initial benchmark');
        } else if (!result.success) {
          this.logger.warn({ error: result.error }, 'Inspect: pipeline failed - no benchmark written');
        }
      }
    }

    // Resolve GH script path
    const ghScript = path.join(this.config.ghScriptsDir, `${algoPart}.gh`);
    const ghScriptAbs = path.resolve(ghScript);

    if (!fs.existsSync(ghScriptAbs)) {
      this.logger.error({ ghScript: ghScriptAbs }, 'Inspect: Grasshopper script not found');
      process.exit(1);
    }

    if (!this.config.rhinoCodeCli || !this.config.rhinoCli) {
      this.logger.error('Inspect: RHINOCODE_CLI or RHINO_CLI not configured');
      process.exit(1);
    }

    // Launch Rhino
    this.logger.info('Inspect: ensuring Rhino is running');
    const rhinoRunning = await ensureRhinoRunning(
      this.config.rhinoCodeCli,
      this.config.rhinoCli,
      this.logger
    );

    if (!rhinoRunning) {
      this.logger.error('Inspect: failed to start Rhino');
      process.exit(1);
    }

    await new Promise(resolve => setTimeout(resolve, 3000));

    // Open Grasshopper window
    this.logger.info('Inspect: opening Grasshopper window');
    try {
      await executeRhinoCommand(
        this.config.rhinoCodeCli,
        '-_Grasshopper _Window _Show _EnterEnd',
        { timeout: 10000 },
        this.logger
      );
    } catch (err: any) {
      this.logger.warn({ error: err?.message }, 'Inspect: Grasshopper window command completed');
    }

    await new Promise(resolve => setTimeout(resolve, 2000));

    // Open the GH script
    const commandString = `-_Grasshopper _Document _Open ${ghScriptAbs} _EnterEnd`;
    this.logger.info({ commandString }, 'Inspect: opening Grasshopper script');

    try {
      await executeRhinoCommand(this.config.rhinoCodeCli, commandString, {}, this.logger);
      this.logger.info('Inspect: Grasshopper script opened');
    } catch (err: any) {
      this.logger.warn({ error: err?.message }, 'Inspect: command completed (may need manual verification)');
    }

    this.logger.info({
      id: job.id,
      objectID: job.objectID,
      algorithm: algoPart,
    }, 'Inspect: complete - Grasshopper is open for manual inspection');
    process.exit(0);
  }

  /**
   * Capture mode: fetch a job by objectID or UUID from the server,
   * save it as a test fixture (.fixture.json), then run the pipeline.
   * If the run succeeds and no .benchmark.json exists yet, write one.
   * Always writes a .result.json for manual inspection.
   */
  async capture(jobIdentifier: string) {
    const fixturesDir = path.resolve('test-fixtures');
    fs.mkdirSync(fixturesDir, { recursive: true });

    this.logger.info({ jobIdentifier }, 'Capture: fetching job from server');
    const resp = await this.http.get(`/api/geometry-processing/job-by-id/${encodeURIComponent(jobIdentifier)}`);

    if (resp.status === 404) {
      this.logger.error({ jobIdentifier }, 'Capture: job not found');
      process.exit(1);
    }
    if (resp.status !== 200) {
      this.logger.error({ status: resp.status }, 'Capture: failed to fetch job');
      process.exit(1);
    }

    const job = resp.data as any;
    const algoPart = job.GeometryAlgorithmName || 'algorithm';
    const objectID = job.objectID || jobIdentifier;
    const fixtureName = `${algoPart}_${objectID}`.replace(/[^a-zA-Z0-9._-]/g, '_');

    // Write .fixture.json (the input payload GH scripts expect)
    const fixturePayload = {
      id: job.id || `capture_${Date.now()}`,
      algorithm: algoPart,
      params: job.GeometryInputParameterData,
      metadata: {
        GeometryName: job.GeometryName,
        CustomerNote: job.JobNote,
        objectID,
      }
    };
    const fixturePath = path.join(fixturesDir, `${fixtureName}.fixture.json`);
    fs.writeFileSync(fixturePath, JSON.stringify(fixturePayload, null, 2), 'utf8');
    this.logger.info({ fixturePath }, 'Capture: wrote fixture file');

    // Run the pipeline against this fixture
    const result = await this.runFixture(fixtureName, fixturePayload);

    // Write .result.json (always)
    const resultPath = path.join(fixturesDir, `${fixtureName}.result.json`);
    fs.writeFileSync(resultPath, JSON.stringify(result, null, 2), 'utf8');
    this.logger.info({ resultPath, success: result.success }, 'Capture: wrote result file');

    // Write .benchmark.json only if: run succeeded AND no benchmark exists yet
    const benchmarkPath = path.join(fixturesDir, `${fixtureName}.benchmark.json`);
    if (result.success && !fs.existsSync(benchmarkPath)) {
      fs.writeFileSync(benchmarkPath, JSON.stringify(result, null, 2), 'utf8');
      this.logger.info({ benchmarkPath }, 'Capture: wrote initial benchmark (protected from overwrite)');
    } else if (result.success && fs.existsSync(benchmarkPath)) {
      this.logger.info({ benchmarkPath }, 'Capture: benchmark already exists - not overwriting');
    } else {
      this.logger.warn('Capture: run failed - no benchmark written');
    }

    process.exit(result.success ? 0 : 1);
  }

  /**
   * Test mode: run all fixtures in test-fixtures/ through the pipeline,
   * compare results against benchmarks, report pass/fail.
   */
  async testAll(filter?: string) {
    const fixturesDir = path.resolve('test-fixtures');
    if (!fs.existsSync(fixturesDir)) {
      this.logger.error('No test-fixtures/ directory found');
      process.exit(1);
    }

    let fixtureFiles = fs.readdirSync(fixturesDir)
      .filter(f => f.endsWith('.fixture.json'))
      .sort();

    if (filter) {
      const match = fixtureFiles.find(f => f.toLowerCase().includes(filter.toLowerCase()));
      if (!match) {
        this.logger.error({ filter }, 'No fixture matching filter');
        process.exit(1);
      }
      fixtureFiles = [match];
    }

    if (fixtureFiles.length === 0) {
      this.logger.warn('No .fixture.json files found in test-fixtures/');
      process.exit(0);
    }

    // Clean stale .result.json files and summary before running
    for (const ff of fixtureFiles) {
      const staleResult = path.join(fixturesDir, ff.replace('.fixture.json', '.result.json'));
      if (fs.existsSync(staleResult)) fs.unlinkSync(staleResult);
    }
    const summaryPath = path.join(fixturesDir, 'LAST_FULL_RUN.txt');
    if (fs.existsSync(summaryPath)) fs.unlinkSync(summaryPath);

    this.logger.info({ count: fixtureFiles.length, filter: filter || 'all' }, 'Test: running fixtures');

    const suiteStartTime = Date.now();
    const results: TestResultEntry[] = [];

    for (const fixtureFile of fixtureFiles) {
      const fixtureName = fixtureFile.replace('.fixture.json', '');
      this.logger.info({ fixtureName }, '--- Running fixture ---');

      // Read fixture
      const fixturePayload = JSON.parse(
        fs.readFileSync(path.join(fixturesDir, fixtureFile), 'utf8')
      );

      // Run pipeline
      const result = await this.runFixture(fixtureName, fixturePayload);

      // Write .result.json (always)
      const resultPath = path.join(fixturesDir, `${fixtureName}.result.json`);
      fs.writeFileSync(resultPath, JSON.stringify(result, null, 2), 'utf8');

      // Compare against benchmark (single read)
      const benchmarkPath = path.join(fixturesDir, `${fixtureName}.benchmark.json`);
      const issues: string[] = [];
      let isFirstBenchmark = false;
      let benchmark: FixtureResult | null = null;

      if (!fs.existsSync(benchmarkPath)) {
        if (result.success) {
          fs.writeFileSync(benchmarkPath, JSON.stringify(result, null, 2), 'utf8');
          this.logger.info({ benchmarkPath }, 'Wrote initial benchmark (first successful run)');
          isFirstBenchmark = true;
          benchmark = result; // just-written benchmark is the result itself
        } else {
          issues.push('No benchmark and run failed - cannot establish baseline');
        }
      } else {
        benchmark = JSON.parse(fs.readFileSync(benchmarkPath, 'utf8'));
      }

      // All benchmark comparisons use the single `benchmark` object
      if (benchmark && !isFirstBenchmark) {
        // Success regression
        if (benchmark.success && !result.success) {
          issues.push(`Regression: was success, now failed (${result.error || 'unknown'})`);
        }
        // Mesh count
        if (benchmark.meta?.mesh_count != null && result.meta?.mesh_count != null) {
          if (benchmark.meta.mesh_count !== result.meta.mesh_count) {
            issues.push(`Mesh count changed: ${benchmark.meta.mesh_count} -> ${result.meta.mesh_count}`);
          }
        }
        // Per-mesh comparisons (volume drift, is_closed)
        const bMeshes = benchmark.meta?.meshes ?? [];
        const rMeshes = result.meta?.meshes ?? [];
        for (let i = 0; i < Math.min(bMeshes.length, rMeshes.length); i++) {
          const bVol = bMeshes[i]?.volume_mm3;
          const rVol = rMeshes[i]?.volume_mm3;
          if (bVol != null && rVol != null && bVol > 0) {
            const pctDiff = Math.abs(rVol - bVol) / bVol * 100;
            if (pctDiff > 1.0) {
              issues.push(`Mesh[${i}] volume drift: ${bVol.toFixed(1)} -> ${rVol.toFixed(1)} (${pctDiff.toFixed(1)}%)`);
            }
          }
          const bClosed = bMeshes[i]?.is_closed;
          const rClosed = rMeshes[i]?.is_closed;
          if (bClosed != null && rClosed != null && bClosed !== rClosed) {
            issues.push(`Mesh[${i}] is_closed changed: ${bClosed} -> ${rClosed}`);
          }
        }
        // Slicing regression
        if (benchmark.sliced && !result.sliced) {
          issues.push('Slicing regression: was sliced, now missing .gcode.3mf');
        }
      }

      // Compute summary values from result and benchmark
      const volume = result.meta?.meshes?.[0]?.volume_mm3;
      let volumePctDiff: number | undefined;
      if (benchmark && volume != null) {
        const bVol = benchmark.meta?.meshes?.[0]?.volume_mm3;
        if (bVol != null && bVol > 0) {
          volumePctDiff = ((volume - bVol) / bVol) * 100;
        }
      }
      const benchmarkElapsed = (!isFirstBenchmark && benchmark?.durationSeconds) || undefined;

      const pass = result.success && issues.length === 0;
      results.push({ name: fixtureName, pass, issues, volume, volumePctDiff, elapsed: result.durationSeconds, benchmarkElapsed, unionMethod: result.unionMethod, sliced: result.sliced, printSizeBytes: result.printSizeBytes, sliceDuration: result.sliceDurationSeconds, isFirstBenchmark });
      this.logger.info({ fixtureName, pass, issues }, pass ? 'PASS' : 'FAIL');
    }

    // Summary
    const suiteDurationSeconds = (Date.now() - suiteStartTime) / 1000;
    const passed = results.filter(r => r.pass).length;
    const failed = results.filter(r => !r.pass).length;
    this.logger.info('');
    this.logger.info({ passed, failed, total: results.length, durationSeconds: Math.round(suiteDurationSeconds * 10) / 10 }, '=== Test Summary ===');

    // Build detail string for a result entry
    const formatDetail = (r: typeof results[number]) => {
      let detail = '';
      if (r.volume != null) {
        detail += ` vol:${r.volume.toFixed(0)}`;
      }
      if (r.volumePctDiff != null) {
        const sign = r.volumePctDiff >= 0 ? '+' : '';
        detail += ` vol_d:${sign}${r.volumePctDiff.toFixed(2)}%`;
      }
      if (r.elapsed != null) {
        detail += ` time:${r.elapsed.toFixed(1)}s`;
        if (r.isFirstBenchmark) {
          detail += ' time_d:first';
        } else if (r.benchmarkElapsed != null && r.benchmarkElapsed > 0) {
          const timeDelta = ((r.elapsed - r.benchmarkElapsed) / r.benchmarkElapsed) * 100;
          const timeSign = timeDelta >= 0 ? '+' : '';
          detail += ` time_d:${timeSign}${timeDelta.toFixed(0)}%`;
        }
      }
      if (r.sliced != null) {
        detail += r.sliced ? ` sliced:${r.sliceDuration != null ? r.sliceDuration.toFixed(1) + 's' : 'yes'}` : ' sliced:NO';
      }
      if (r.unionMethod) {
        detail += ` [${r.unionMethod}]`;
      }
      return detail;
    };

    for (const r of results) {
      const icon = r.pass ? 'PASS' : 'FAIL';
      this.logger.info(`  [${icon}] ${r.name}${formatDetail(r)}`);
      for (const issue of r.issues) {
        this.logger.info(`         ${issue}`);
      }
    }

    // Write committed summary file when running all fixtures (no filter)
    if (!filter) {
      const lines: string[] = [];
      lines.push(`Test Run: ${new Date().toISOString()}`);
      lines.push(`Result: ${failed === 0 ? 'ALL PASSED' : `${failed} FAILED`}`);
      lines.push(`Fixtures: ${passed} passed, ${failed} failed, ${results.length} total`);
      const mins = Math.floor(suiteDurationSeconds / 60);
      const secs = Math.round(suiteDurationSeconds % 60);
      lines.push(`Duration: ${mins > 0 ? `${mins}m ${secs}s` : `${secs}s`}`);
      lines.push('');
      for (const r of results) {
        const icon = r.pass ? 'PASS' : 'FAIL';
        lines.push(`  [${icon}] ${r.name}${formatDetail(r)}`);
        for (const issue of r.issues) {
          lines.push(`         ${issue}`);
        }
      }
      lines.push('');
      fs.writeFileSync(summaryPath, lines.join('\n'), 'utf8');
      this.logger.info({ summaryPath }, 'Wrote test summary (committable)');
    }

    process.exit(failed > 0 ? 1 : 0);
  }

  /**
   * Run a single fixture through the pipeline. Returns a result object
   * with success, timing, meta, log snippet, and union method (if found in log).
   */
  private async runFixture(
    fixtureName: string,
    fixturePayload: { id: string; algorithm: string; params: any; metadata?: any }
  ): Promise<FixtureResult> {
    const baseName = fixtureName;
    const algoPart = fixturePayload.algorithm;

    // Clean inbox/outbox
    for (const dir of [this.inbox, this.outbox]) {
      try {
        for (const f of fs.readdirSync(dir)) {
          fs.unlinkSync(path.join(dir, f));
        }
      } catch {}
    }

    // Write input JSON to inbox
    const inboxJson = path.join(this.inbox, `${baseName}.json`);
    fs.writeFileSync(inboxJson, JSON.stringify(fixturePayload, null, 2), 'utf8');

    const startTime = Date.now();
    let success = false;
    let error: string | undefined;
    let meta: any = undefined;
    let unionMethod: string | undefined;
    let logSnippet: string | undefined;
    let sliceDurationSeconds: number | undefined;

    try {
      const outputs = await runPipeline({
        id: String(fixturePayload.id),
        algorithm: String(algoPart),
        params: fixturePayload.params,
        ghScriptsDir: this.config.ghScriptsDir,
        outboxDir: this.outbox,
        baseName,
        inboxJsonPath: inboxJson,
        rhinoCli: this.config.rhinoCli,
        rhinoCodeCli: this.config.rhinoCodeCli,
        bambuCli: this.config.bambuCli,
        dryRun: this.config.dryRun,
        logger: this.logger,
      });
      success = true;
      sliceDurationSeconds = outputs.sliceDurationSeconds;
    } catch (err: any) {
      error = err?.message || 'Unknown error';
      this.logger.error({ error }, 'Fixture run failed');
    }

    const durationSeconds = (Date.now() - startTime) / 1000;

    // Read .meta.json if present
    const metaPath = path.join(this.outbox, `${baseName}.meta.json`);
    if (fs.existsSync(metaPath)) {
      try {
        meta = JSON.parse(fs.readFileSync(metaPath, 'utf8'));
      } catch {}
    }

    // Check for sliced .gcode.3mf output
    let sliced = false;
    let printSizeBytes: number | undefined;
    const printPath = path.join(this.outbox, `${baseName}.gcode.3mf`);
    if (fs.existsSync(printPath)) {
      try {
        const printStats = fs.statSync(printPath);
        if (printStats.size > 0) {
          sliced = true;
          printSizeBytes = printStats.size;
        }
      } catch {}
    }

    // Read log.txt for union method and snippet
    const logPath = path.join(this.outbox, 'log.txt');
    if (fs.existsSync(logPath)) {
      try {
        const logContent = fs.readFileSync(logPath, 'utf8');
        // Extract union method from log (e.g. "SUCCESS - Clean multi-brep union")
        const successMatch = logContent.match(/SUCCESS - (.+)/);
        if (successMatch) {
          unionMethod = successMatch[1].trim();
        }
        // Keep last 2000 chars as snippet
        logSnippet = logContent.length > 2000
          ? logContent.slice(-2000)
          : logContent;
      } catch {}
    }

    return {
      fixtureName,
      success,
      ...(error && { error }),
      durationSeconds: Math.round(durationSeconds * 100) / 100,
      ...(meta && { meta }),
      ...(unionMethod && { unionMethod }),
      sliced,
      ...(printSizeBytes != null && { printSizeBytes }),
      ...(sliceDurationSeconds != null && { sliceDurationSeconds }),
      ...(logSnippet && { logSnippet }),
      timestamp: new Date().toISOString(),
    };
  }
}
