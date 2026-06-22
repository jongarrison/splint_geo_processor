#!/usr/bin/env tsx
// Rhino uptime visualizer.
//
// Walks pino JSON log lines from ~/SplintFactoryFiles/logs/processor-*.log,
// detects Rhino UP/DOWN transitions, and prints an ASCII timeline + summary
// over a configurable window (default last 24 hours).
//
// Usage:
//   npm run rhino-uptime                # last 24h
//   npm run rhino-uptime -- --hours=72  # last 72h
//   npm run rhino-uptime -- --verbose   # include transition list

import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';

type State = 'UP' | 'DOWN' | 'UNKNOWN';

interface Transition {
  time: number;
  state: State;
  msg: string;
}

interface Interval {
  start: number;
  end: number;
  state: State;
}

// Substring matches against the pino `msg` field.
// `\u00b7` = middle dot used in the DOWN bar character; safe to read above.
const UP_PATTERNS = [
  'Rhino pre-warm complete',
  'Rhino is already running and responding',
  'Phase 1: Rhino started successfully',
  'Phase 2: Rhino started successfully',
  'Rhino became healthy during startup grace window',
  'Rhino warm', // poll-loop proactive warm success ([env] Rhino warm)
];

const DOWN_PATTERNS = [
  'Rhino exited cleanly',
  'Rhino force killed',
  'Rhino not running; nothing to close',
];

// Any pino JSON line resets the "last seen" cursor so we can detect gaps
// where the processor itself was down (no logs at all = state UNKNOWN).
const HEARTBEAT_GAP_MS = 5 * 60 * 1000;

const BAR_CELLS = 80;
const CHAR_BY_STATE: Record<State, string> = {
  UP: '\u2588',     // full block
  DOWN: '\u00b7',   // middle dot
  UNKNOWN: '?',
};

function parseArgs(): { hours: number; verbose: boolean; logsDir: string } {
  let hours = 24;
  let verbose = false;
  let logsDir = path.join(os.homedir(), 'SplintFactoryFiles', 'logs');
  for (const arg of process.argv.slice(2)) {
    const hoursMatch = arg.match(/^--hours=(\d+(?:\.\d+)?)$/);
    if (hoursMatch) { hours = Number(hoursMatch[1]); continue; }
    if (arg === '--verbose' || arg === '-v') { verbose = true; continue; }
    const dirMatch = arg.match(/^--logs-dir=(.+)$/);
    if (dirMatch) { logsDir = dirMatch[1]; continue; }
    console.error(`Unknown arg: ${arg}`);
    process.exit(2);
  }
  if (!Number.isFinite(hours) || hours <= 0) {
    console.error('--hours must be a positive number');
    process.exit(2);
  }
  return { hours, verbose, logsDir };
}

// Pick log files whose mtime could overlap [windowStartMs - 2 days, now].
// The 2-day cushion captures the "seed" file holding the last transition
// before the window opens.
function selectLogFiles(logsDir: string, windowStartMs: number): string[] {
  if (!fs.existsSync(logsDir)) {
    console.error(`Logs directory not found: ${logsDir}`);
    process.exit(1);
  }
  const cutoff = windowStartMs - 2 * 24 * 60 * 60 * 1000;
  const all = fs.readdirSync(logsDir)
    .filter(f => /^processor-\d{4}-\d{2}-\d{2}\.log$/.test(f))
    .map(f => path.join(logsDir, f));
  const eligible = all.filter(f => {
    try { return fs.statSync(f).mtimeMs >= cutoff; } catch { return false; }
  });
  // Sort by filename date so chronological order is stable.
  return eligible.sort();
}

// Classify a pino log line into UP/DOWN/null. We only react to the first
// matching pattern; UP wins over DOWN since they are mutually exclusive.
function classify(msg: string): State | null {
  for (const p of UP_PATTERNS) if (msg.includes(p)) return 'UP';
  for (const p of DOWN_PATTERNS) if (msg.includes(p)) return 'DOWN';
  return null;
}

interface ParsedLogs {
  transitions: Transition[]; // strictly time-ordered
  allTimestamps: number[];   // every parseable line's time, for gap detection
}

function parseLogs(files: string[]): ParsedLogs {
  const transitions: Transition[] = [];
  const allTimestamps: number[] = [];
  for (const file of files) {
    const raw = fs.readFileSync(file, 'utf8');
    for (const line of raw.split('\n')) {
      if (!line.trim()) continue;
      let obj: any;
      try { obj = JSON.parse(line); } catch { continue; }
      const t = typeof obj?.time === 'number' ? obj.time : null;
      const msg = typeof obj?.msg === 'string' ? obj.msg : '';
      if (t === null) continue;
      allTimestamps.push(t);
      const state = classify(msg);
      if (state) transitions.push({ time: t, state, msg });
    }
  }
  transitions.sort((a, b) => a.time - b.time);
  allTimestamps.sort((a, b) => a - b);
  return { transitions, allTimestamps };
}

// Build the list of intervals covering [windowStart, windowEnd] using the
// transition list plus heartbeat-gap detection.
function buildIntervals(
  parsed: ParsedLogs,
  windowStart: number,
  windowEnd: number,
): Interval[] {
  const { transitions, allTimestamps } = parsed;

  // Seed state: most recent transition strictly before windowStart.
  let currentState: State = 'UNKNOWN';
  for (const t of transitions) {
    if (t.time < windowStart) currentState = t.state;
    else break;
  }

  // Locate the index of the first timestamp >= windowStart for gap walks.
  // We also include the last timestamp before windowStart as our seed lastSeen.
  let lastSeen = -Infinity;
  for (const ts of allTimestamps) {
    if (ts < windowStart) lastSeen = ts;
    else break;
  }

  // Walk events inside the window.
  const inWindow = transitions.filter(t => t.time >= windowStart && t.time <= windowEnd);

  // Also walk *all* timestamps inside the window so we can detect heartbeat
  // gaps (processor itself was offline). We interleave them with transitions.
  const tsInWindow = allTimestamps.filter(ts => ts >= windowStart && ts <= windowEnd);

  const intervals: Interval[] = [];
  let cursor = windowStart;

  // Helper: close out current interval up to `to` and start the next at `to`.
  const flush = (to: number, nextState: State) => {
    if (to > cursor) intervals.push({ start: cursor, end: to, state: currentState });
    cursor = to;
    currentState = nextState;
  };

  // Walk timestamps to detect gaps; whenever a gap > HEARTBEAT_GAP_MS appears,
  // flip to UNKNOWN starting at `lastSeen + HEARTBEAT_GAP_MS` and back to the
  // pre-gap state once logs resume (until/unless a transition occurs).
  const transIdx = { i: 0 };
  for (const ts of tsInWindow) {
    // Drain transitions strictly before this timestamp.
    while (transIdx.i < inWindow.length && inWindow[transIdx.i].time < ts) {
      const t = inWindow[transIdx.i++];
      flush(t.time, t.state);
    }
    // Heartbeat-gap check.
    if (lastSeen !== -Infinity && ts - lastSeen > HEARTBEAT_GAP_MS) {
      const gapStart = Math.max(cursor, lastSeen + HEARTBEAT_GAP_MS);
      if (gapStart > cursor) {
        intervals.push({ start: cursor, end: gapStart, state: currentState });
        cursor = gapStart;
      }
      // Mark the gap itself as UNKNOWN.
      if (ts > cursor) {
        intervals.push({ start: cursor, end: ts, state: 'UNKNOWN' });
        cursor = ts;
      }
      // Note: currentState is unchanged; if no transition happens after the
      // gap, we resume the pre-gap state (the processor came back up and
      // Rhino is presumably still in whatever state it was).
    }
    lastSeen = ts;
  }
  // Drain any remaining transitions inside the window.
  while (transIdx.i < inWindow.length) {
    const t = inWindow[transIdx.i++];
    flush(t.time, t.state);
  }

  // Final gap check at the end of the window.
  if (lastSeen !== -Infinity && windowEnd - lastSeen > HEARTBEAT_GAP_MS) {
    const gapStart = Math.max(cursor, lastSeen + HEARTBEAT_GAP_MS);
    if (gapStart > cursor) {
      intervals.push({ start: cursor, end: gapStart, state: currentState });
      cursor = gapStart;
    }
    intervals.push({ start: cursor, end: windowEnd, state: 'UNKNOWN' });
    cursor = windowEnd;
  }

  // Close out tail.
  if (windowEnd > cursor) intervals.push({ start: cursor, end: windowEnd, state: currentState });

  // Merge adjacent intervals of identical state.
  const merged: Interval[] = [];
  for (const iv of intervals) {
    const last = merged[merged.length - 1];
    if (last && last.state === iv.state && last.end === iv.start) {
      last.end = iv.end;
    } else {
      merged.push({ ...iv });
    }
  }
  return merged;
}

// Render an 80-cell bar where each cell shows the state covering the
// majority of its time slice.
function renderBar(intervals: Interval[], windowStart: number, windowEnd: number): string {
  const cellMs = (windowEnd - windowStart) / BAR_CELLS;
  const out: string[] = [];
  for (let i = 0; i < BAR_CELLS; i++) {
    const cellStart = windowStart + i * cellMs;
    const cellEnd = cellStart + cellMs;
    const tally: Record<State, number> = { UP: 0, DOWN: 0, UNKNOWN: 0 };
    for (const iv of intervals) {
      const overlap = Math.max(0, Math.min(iv.end, cellEnd) - Math.max(iv.start, cellStart));
      tally[iv.state] += overlap;
    }
    let best: State = 'UNKNOWN';
    let bestVal = -1;
    for (const s of ['UP', 'DOWN', 'UNKNOWN'] as State[]) {
      if (tally[s] > bestVal) { bestVal = tally[s]; best = s; }
    }
    out.push(CHAR_BY_STATE[best]);
  }
  return out.join('');
}

function fmtDuration(ms: number): string {
  if (ms < 0) ms = 0;
  const totalSec = Math.round(ms / 1000);
  const h = Math.floor(totalSec / 3600);
  const m = Math.floor((totalSec % 3600) / 60);
  const s = totalSec % 60;
  if (h > 0) return `${h}h ${m}m`;
  if (m > 0) return `${m}m ${s}s`;
  return `${s}s`;
}

function fmtTime(ms: number): string {
  const d = new Date(ms);
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function main() {
  const { hours, verbose, logsDir } = parseArgs();
  const windowEnd = Date.now();
  const windowStart = windowEnd - hours * 60 * 60 * 1000;

  const files = selectLogFiles(logsDir, windowStart);
  if (files.length === 0) {
    console.error(`No processor logs found in ${logsDir}`);
    process.exit(1);
  }

  const parsed = parseLogs(files);
  const intervals = buildIntervals(parsed, windowStart, windowEnd);

  // Tally totals.
  const totals: Record<State, number> = { UP: 0, DOWN: 0, UNKNOWN: 0 };
  for (const iv of intervals) totals[iv.state] += iv.end - iv.start;
  const windowMs = windowEnd - windowStart;
  const upPct = (totals.UP / windowMs) * 100;
  const downPct = (totals.DOWN / windowMs) * 100;
  const unkPct = (totals.UNKNOWN / windowMs) * 100;

  const upStarts = parsed.transitions.filter(t => t.state === 'UP' && t.time >= windowStart && t.time <= windowEnd).length;
  const downStops = parsed.transitions.filter(t => t.state === 'DOWN' && t.time >= windowStart && t.time <= windowEnd).length;

  console.log('Rhino uptime');
  console.log('============');
  console.log(`Window:   ${fmtTime(windowStart)} -> ${fmtTime(windowEnd)} (${hours}h)`);
  console.log(`Logs dir: ${logsDir}`);
  console.log(`Files:    ${files.length} (${files.map(f => path.basename(f)).join(', ')})`);
  console.log(`Lines:    ${parsed.allTimestamps.length} parsed, ${parsed.transitions.length} Rhino transitions (${upStarts} starts / ${downStops} stops in window)`);
  console.log('');
  console.log(`${renderBar(intervals, windowStart, windowEnd)}`);
  // Axis: start date | midpoint time | end date+time.
  const midMs = windowStart + (windowEnd - windowStart) / 2;
  const leftLabel = fmtTime(windowStart);
  const midLabel = fmtTime(midMs).slice(-5);
  const rightLabel = fmtTime(windowEnd);
  const axis = leftLabel.padEnd(BAR_CELLS / 2 - Math.floor(midLabel.length / 2))
    + midLabel
    + rightLabel.padStart(BAR_CELLS - (BAR_CELLS / 2 - Math.floor(midLabel.length / 2)) - midLabel.length);
  console.log(`${axis}`);
  console.log('');
  console.log(`Legend: ${CHAR_BY_STATE.UP} up   ${CHAR_BY_STATE.DOWN} down   ${CHAR_BY_STATE.UNKNOWN} unknown (no logs / no transitions yet)`);
  console.log('');
  console.log(`Up:      ${fmtDuration(totals.UP).padEnd(10)} (${upPct.toFixed(1)}%)`);
  console.log(`Down:    ${fmtDuration(totals.DOWN).padEnd(10)} (${downPct.toFixed(1)}%)`);
  console.log(`Unknown: ${fmtDuration(totals.UNKNOWN).padEnd(10)} (${unkPct.toFixed(1)}%)`);

  if (parsed.transitions.length === 0) {
    console.log('');
    console.log('Note: no Rhino start/stop signals found in any log file.');
    console.log('      Either the processor has not run Rhino yet, or the log files');
    console.log('      do not cover a period when Rhino was used.');
  }

  if (verbose) {
    console.log('');
    console.log('Transitions in window:');
    const inWindow = parsed.transitions.filter(t => t.time >= windowStart && t.time <= windowEnd);
    if (inWindow.length === 0) {
      console.log('  (none)');
    } else {
      for (const t of inWindow) {
        console.log(`  ${fmtTime(t.time)}  ${t.state.padEnd(7)} ${t.msg}`);
      }
    }
  }
}

main();
