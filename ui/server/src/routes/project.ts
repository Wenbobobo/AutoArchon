/** Project, progress, tasks, sorry count — core project state routes */
import fs from 'fs';
import path from 'path';
import type { FastifyInstance } from 'fastify';
import type { ProgressData, Task } from '../types.js';
import { readFileOr } from '../utils.js';

export interface ProjectPaths {
  projectPath: string;
  archonPath: string;
  logsPath: string;
}

function parseProgressMarkdown(content: string): ProgressData {
  const stageMatch = content.match(/## Current Stage\s*\n\s*(\S+)/);
  const stage = stageMatch?.[1] || 'init';

  const objectives: string[] = [];
  const objSection = content.match(/## Current Objectives\s*\n([\s\S]*?)(?=\n## |\n# |$)/);
  if (objSection) {
    for (const line of objSection[1].split('\n')) {
      const m = line.match(/^\s*\d+\.\s+(.+)/);
      if (m) objectives.push(m[1].trim());
    }
  }

  const checklist: { label: string; done: boolean }[] = [];
  const stagesSection = content.match(/## Stages\s*\n([\s\S]*?)(?=\n## |\n# |$)/);
  if (stagesSection) {
    for (const line of stagesSection[1].split('\n')) {
      const m = line.match(/^\s*-\s*\[([ xX])\]\s*(.+)/);
      if (m) checklist.push({ label: m[2].trim(), done: m[1] !== ' ' });
    }
  }
  return { stage, objectives, checklist };
}

function parseTasksMarkdown(content: string, status: 'pending' | 'done'): Task[] {
  const tasks: Task[] = [];
  let currentFile = '';
  for (const line of content.split('\n')) {
    const fileMatch = line.match(/^## (.+\.lean)/);
    if (fileMatch) { currentFile = fileMatch[1].trim(); continue; }
    const thMatch = line.match(/^### (.+)/);
    if (thMatch) {
      tasks.push({ id: '', theorem: thMatch[1].trim(), file: currentFile, status, proofSketch: '' });
    }
  }
  return tasks.map((t, i) => ({ ...t, id: `task-${status}-${i}` }));
}

function countSorries(projectPath: string): { file: string; count: number; lines: number[] }[] {
  const results: { file: string; count: number; lines: number[] }[] = [];
  function walk(d: string) {
    for (const entry of fs.readdirSync(d, { withFileTypes: true })) {
      const full = path.join(d, entry.name);
      if (entry.isDirectory() && !['_lake', '.lake', '.archon', 'node_modules'].includes(entry.name)) walk(full);
      else if (entry.isFile() && entry.name.endsWith('.lean')) {
        const content = fs.readFileSync(full, 'utf-8');
        const fileLines = content.split('\n');
        const sorryLines: number[] = [];
        fileLines.forEach((line, idx) => {
          if (/\bsorry\b/.test(line) && !line.trimStart().startsWith('--')) sorryLines.push(idx + 1);
        });
        if (sorryLines.length > 0) {
          results.push({ file: path.relative(projectPath, full), count: sorryLines.length, lines: sorryLines });
        }
      }
    }
  }
  try { walk(projectPath); } catch { /* ignore permission errors */ }
  return results;
}

export function register(fastify: FastifyInstance, paths: ProjectPaths) {
  const { projectPath, archonPath } = paths;
  const taskResultsPath = path.join(archonPath, 'task_results');

  fastify.get('/api/project', async () => ({
    name: path.basename(projectPath),
    path: projectPath,
    archonPath,
  }));

  fastify.get('/api/progress', async () => {
    const content = readFileOr(path.join(archonPath, 'PROGRESS.md'), '');
    return parseProgressMarkdown(content);
  });

  fastify.get('/api/tasks', async () => {
    const pending = parseTasksMarkdown(readFileOr(path.join(archonPath, 'task_pending.md'), ''), 'pending');
    const done = parseTasksMarkdown(readFileOr(path.join(archonPath, 'task_done.md'), ''), 'done');
    return [...pending, ...done];
  });

  fastify.get('/api/sorry-count', async () => {
    const files = countSorries(projectPath);
    const total = files.reduce((s, f) => s + f.count, 0);
    return { total, files };
  });

  fastify.get('/api/task-results', async () => {
    if (!fs.existsSync(taskResultsPath)) return [];
    return fs.readdirSync(taskResultsPath).filter(f => f.endsWith('.md')).map(f => ({
      name: f, content: fs.readFileSync(path.join(taskResultsPath, f), 'utf-8'),
    }));
  });
}
