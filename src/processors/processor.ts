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
          this.logger.debug('No jobs available');
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
        this.logger.info('Received job');
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
            CustomerID: job?.CustomerID
          }
        };
        fs.writeFileSync(inboxJson, JSON.stringify(inputPayload, null, 2), 'utf8');
        this.logger.info({ inboxJson }, 'Wrote input JSON to inbox');

        // Single-threaded processing section: pause polling while we process this job
        try {
          this.logger.info({ id: idPart, algo: algoPart }, 'Starting geometry processing');
          const outputs = await runPipeline({
            id: String(idPart),
            algorithm: String(algoPart),
            params: inputPayload.params,
            ghScriptsDir: this.config.ghScriptsDir,
            outboxDir: this.outbox,
            rhinoCli: this.config.rhinoCli,
            rhinoCodeCli: this.config.rhinoCodeCli,
            bambuCli: this.config.bambuCli,
            dryRun: this.config.dryRun,
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

          await this.reportSuccess(idPart, geometryB64, geometryName, printB64, printName);
        } catch (procErr: any) {
          this.logger.error({ err: procErr?.message }, 'Processing failed');
          await this.reportResult(idPart, false, String(procErr?.message || 'Processing failed'));
        } finally {
          this.logger.info({ id: idPart }, 'Finished processing');
        }
      } catch (err) {
        this.logger.error({ err }, 'Processor iteration failed');
      }
      // throttle loop regardless of outcome
      await sleep(intervalMs);
    }
  }

  private async reportResult(jobId: string, isSuccess: boolean, errorMessage?: string) {
    const payload: any = {
      GeometryProcessingQueueID: jobId,
      isSuccess,
    };
    if (!isSuccess && errorMessage) payload.errorMessage = errorMessage;
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

  private async reportSuccess(jobId: string, geometryB64: string, geometryName: string, printB64?: string, printName?: string) {
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
}
