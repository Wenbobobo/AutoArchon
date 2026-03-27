import { useState } from 'react';
import type { LogEntry } from '../types';
import { fmtTime, truncate } from '../utils/format';
import styles from './LogEntryLine.module.css';

const EVENT_COLORS: Record<string, string> = {
  shell: 'var(--blue)', thinking: 'var(--text-muted)', tool_call: 'var(--purple)',
  tool_result: 'var(--orange)', text: 'var(--green)', session_end: 'var(--green)',
};

interface Props { entry: LogEntry; }

export default function LogEntryLine({ entry }: Props) {
  const [expanded, setExpanded] = useState(false);

  let headline = '';
  let detail = '';
  let hasDetail = false;

  switch (entry.event) {
    case 'shell':
      headline = entry.message || '';
      break;
    case 'thinking': {
      const thinkContent = entry.content || '';
      const thinkTrunc = truncate(thinkContent, 200);
      headline = thinkTrunc.text;
      if (thinkTrunc.truncated || thinkContent.includes('\n')) { detail = thinkContent; hasDetail = true; }
      break;
    }
    case 'text': {
      const textContent = entry.content || entry.message || '';
      const textTrunc = truncate(textContent, 200);
      headline = textTrunc.text;
      if (textTrunc.truncated || textContent.includes('\n')) { detail = textContent; hasDetail = true; }
      break;
    }
    case 'tool_call': {
      const toolName = entry.tool || '?';
      let argSummary = '';
      if (entry.input) {
        const inp = entry.input as Record<string, unknown>;
        if (inp.command) argSummary = String(inp.command);
        else if (inp.file_path) argSummary = String(inp.file_path);
        else if (inp.path) argSummary = String(inp.path);
        else if (inp.pattern) argSummary = String(inp.pattern);
        else if (inp.old_string) argSummary = `${inp.file_path || ''} (edit)`;
        else {
          const firstVal = Object.values(inp).find(v => typeof v === 'string');
          if (firstVal) argSummary = String(firstVal).slice(0, 120);
        }
      }
      headline = argSummary ? `${toolName}: ${argSummary}` : toolName;
      if (entry.input) { detail = JSON.stringify(entry.input, null, 2); hasDetail = true; }
      break;
    }
    case 'tool_result': {
      const content = entry.content || entry.message || '';
      const t = truncate(content, 150);
      headline = t.text;
      if (t.truncated || content.includes('\n')) { detail = content; hasDetail = true; }
      break;
    }
    case 'session_end': {
      const dur = entry.duration_ms ? `${(entry.duration_ms / 1000).toFixed(0)}s` : '';
      let model = '';
      if (entry.model_usage) {
        const fullName = Object.keys(entry.model_usage)[0] || '';
        // Shorten: claude-opus-4-20250514 → opus-4, claude-sonnet-4-... → sonnet-4
        model = fullName.replace(/^claude-/, '').replace(/-\d{8}$/, '');
      }
      const parts = ['Session end'];
      if (model) parts.push(model);
      if (dur) parts.push(dur);
      if (entry.num_turns) parts.push(`${entry.num_turns} turns`);
      if (entry.total_cost_usd) parts.push(`$${entry.total_cost_usd.toFixed(2)}`);
      headline = parts.join(' · ');
      if (entry.summary) { detail = entry.summary; hasDetail = true; }
      break;
    }
  }

  return (
    <div className={styles.line}>
      <span className={styles.ts}>{fmtTime(entry.ts)}</span>
      <span className={styles.event} style={{ color: EVENT_COLORS[entry.event] || 'var(--text-muted)' }}>
        {entry.event}{entry.level === 'error' ? '!' : entry.level === 'warn' ? '⚠' : ''}
      </span>
      <span
        className={`${styles.text} ${hasDetail ? styles.expandable : ''}`}
        onClick={hasDetail ? () => setExpanded(!expanded) : undefined}
      >
        {headline}
        {hasDetail && <span className={styles.expandHint}>{expanded ? ' ▾' : ' ▸'}</span>}
      </span>
      {expanded && detail && <pre className={styles.detail}>{detail}</pre>}
    </div>
  );
}
