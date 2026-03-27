/** Logs API — tree listing, content retrieval, WebSocket streaming */
import fs from 'fs';
import path from 'path';
import type { FastifyInstance } from 'fastify';
import { parseJsonl } from '../utils.js';
import type { ProjectPaths } from './project.js';

interface LogFileEntry { name: string; path: string; size: number; modified: string; role?: string }
interface LogGroup { id: string; files: LogFileEntry[]; meta?: Record<string, unknown> }

function resolveLogPath(logsPath: string, logPath: string): string | null {
  const normalized = path.normalize(logPath).replace(/^(\.\.[/\\])+/, '');
  const full = path.join(logsPath, normalized);
  if (!full.startsWith(logsPath)) return null;
  if (!full.endsWith('.jsonl')) return full + '.jsonl';
  return full;
}

export function register(fastify: FastifyInstance, paths: ProjectPaths) {
  const { logsPath } = paths;

  // Tree-structured log listing
  fastify.get('/api/logs', async () => {
    if (!fs.existsSync(logsPath)) return { flat: [], groups: [] };

    const flat: LogFileEntry[] = fs.readdirSync(logsPath)
      .filter(f => f.endsWith('.jsonl') && fs.statSync(path.join(logsPath, f)).isFile())
      .map(f => {
        const stat = fs.statSync(path.join(logsPath, f));
        return { name: f, path: f, size: stat.size, modified: stat.mtime.toISOString() };
      })
      .sort((a, b) => b.modified.localeCompare(a.modified));

    const groups: LogGroup[] = [];
    const iterDirs = fs.readdirSync(logsPath)
      .filter(d => d.startsWith('iter-') && fs.statSync(path.join(logsPath, d)).isDirectory())
      .sort();

    for (const dir of iterDirs) {
      const dirPath = path.join(logsPath, dir);
      const files: LogFileEntry[] = [];

      for (const f of fs.readdirSync(dirPath).filter(f => f.endsWith('.jsonl') && f !== 'provers-combined.jsonl')) {
        const full = path.join(dirPath, f);
        if (!fs.statSync(full).isFile()) continue;
        const role = f.replace('.jsonl', '');
        const stat = fs.statSync(full);
        files.push({ name: f, path: `${dir}/${f}`, size: stat.size, modified: stat.mtime.toISOString(), role });
      }

      const proversDir = path.join(dirPath, 'provers');
      if (fs.existsSync(proversDir) && fs.statSync(proversDir).isDirectory()) {
        for (const f of fs.readdirSync(proversDir).filter(f => f.endsWith('.jsonl')).sort()) {
          const full = path.join(proversDir, f);
          const stat = fs.statSync(full);
          files.push({ name: f, path: `${dir}/provers/${f}`, size: stat.size, modified: stat.mtime.toISOString(), role: 'prover' });
        }
      }

      let meta: Record<string, unknown> | undefined;
      const metaFile = path.join(dirPath, 'meta.json');
      try { meta = JSON.parse(fs.readFileSync(metaFile, 'utf-8')); } catch { /* skip */ }

      groups.push({ id: dir, files, meta });
    }

    return { flat, groups };
  });

  // Wildcard log content
  fastify.get('/api/logs/*', async (req, reply) => {
    const subpath = (req.params as Record<string, string>)['*'];
    if (!subpath) return reply.status(400).send({ error: 'Missing path' });
    const filePath = resolveLogPath(logsPath, subpath);
    if (!filePath || !fs.existsSync(filePath)) return reply.status(404).send({ error: 'Not found' });
    return parseJsonl(filePath);
  });

  // WebSocket streaming
  fastify.get('/api/log-stream/*', { websocket: true }, (socket, req) => {
    const subpath = (req.params as Record<string, string>)['*'] || '';
    const filePath = resolveLogPath(logsPath, subpath);
    if (!filePath || !fs.existsSync(filePath)) {
      socket.send(JSON.stringify({ type: 'error', message: 'Not found' }));
      socket.close();
      return;
    }

    let lastSize = fs.statSync(filePath).size;
    socket.send(JSON.stringify({ type: 'ready', size: lastSize }));

    const watcher = fs.watch(filePath, () => {
      try {
        const newSize = fs.statSync(filePath).size;
        if (newSize <= lastSize) return;
        const stream = fs.createReadStream(filePath, { start: lastSize, end: newSize - 1, encoding: 'utf-8' });
        let buffer = '';
        stream.on('data', (chunk) => {
          buffer += chunk;
          const lines = buffer.split('\n');
          buffer = lines.pop() || '';
          for (const line of lines) {
            if (line.trim()) try { socket.send(line); } catch { /* ignore */ }
          }
        });
        stream.on('end', () => {
          if (buffer.trim()) try { socket.send(buffer); } catch { /* ignore */ }
        });
        lastSize = newSize;
      } catch { /* ignore stat errors during write */ }
    });

    socket.on('close', () => watcher.close());
  });
}
