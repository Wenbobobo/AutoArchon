import fs from 'fs';
import type { LogEntry } from './types.js';

/** Read a file or return fallback on error */
export function readFileOr(filePath: string, fallback: string): string {
  try { return fs.readFileSync(filePath, 'utf-8'); } catch { return fallback; }
}

/** Parse a .jsonl file into typed entries */
export function parseJsonl(filePath: string): LogEntry[] {
  const content = readFileOr(filePath, '');
  if (!content) return [];
  return content.split('\n').filter(Boolean).map(line => {
    try { return JSON.parse(line) as LogEntry; } catch { return null; }
  }).filter((e): e is LogEntry => e !== null);
}
