/** Iterations API — meta.json based iteration summaries */
import fs from 'fs';
import path from 'path';
import type { FastifyInstance } from 'fastify';
import { parseJsonl } from '../utils.js';
import type { ProjectPaths } from './project.js';

function listIterDirs(logsPath: string): string[] {
  if (!fs.existsSync(logsPath)) return [];
  return fs.readdirSync(logsPath)
    .filter(d => d.startsWith('iter-') && fs.statSync(path.join(logsPath, d)).isDirectory())
    .sort();
}

function readMeta(logsPath: string, iterDir: string): Record<string, unknown> | null {
  const metaFile = path.join(logsPath, iterDir, 'meta.json');
  try { return JSON.parse(fs.readFileSync(metaFile, 'utf-8')); } catch { return null; }
}

export function register(fastify: FastifyInstance, paths: ProjectPaths) {
  const { logsPath } = paths;

  fastify.get('/api/iterations', async () => {
    return listIterDirs(logsPath).map(d => {
      const meta = readMeta(logsPath, d);
      if (!meta) return { id: d };
      return { id: d, ...meta };
    });
  });

  fastify.get<{ Params: { id: string } }>('/api/iterations/:id', async (req, reply) => {
    const iterDir = req.params.id;
    if (!iterDir.startsWith('iter-')) return reply.status(400).send({ error: 'Invalid iteration id' });
    const meta = readMeta(logsPath, iterDir);
    if (!meta) return reply.status(404).send({ error: 'Not found' });

    const proversDir = path.join(logsPath, iterDir, 'provers');
    const proverFiles: { slug: string; size: number }[] = [];
    if (fs.existsSync(proversDir)) {
      for (const f of fs.readdirSync(proversDir).filter(f => f.endsWith('.jsonl'))) {
        const stat = fs.statSync(path.join(proversDir, f));
        proverFiles.push({ slug: f.replace('.jsonl', ''), size: stat.size });
      }
    }
    return { id: iterDir, ...meta, proverFiles };
  });

  fastify.get<{ Params: { id: string; file: string } }>('/api/iterations/:id/provers/:file', async (req, reply) => {
    const { id, file } = req.params;
    if (!id.startsWith('iter-')) return reply.status(400).send({ error: 'Invalid iteration id' });
    const filePath = path.join(logsPath, id, 'provers', file.endsWith('.jsonl') ? file : `${file}.jsonl`);
    if (!fs.existsSync(filePath)) return reply.status(404).send({ error: 'Not found' });
    return parseJsonl(filePath);
  });
}
