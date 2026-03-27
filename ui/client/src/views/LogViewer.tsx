import { useState, useEffect, useMemo } from 'react';
import { useLogs } from '../hooks/useApi';
import { useLogStream } from '../hooks/useLogStream';
import type { LogEntry, LogGroup } from '../types';
import { fmtDuration } from '../utils/format';
import LogEntryLine from '../components/LogEntryLine';
import styles from './LogViewer.module.css';

// --- Sidebar components ---

function PhaseTag({ label, status, secs }: { label: string; status?: string; secs?: number }) {
  if (!status) return null;
  const color = status === 'done' ? 'var(--green)' : status === 'running' ? 'var(--blue)' : status === 'error' ? 'var(--red)' : 'var(--text-muted)';
  return (
    <span className={styles.phase}>
      <span className={styles.phaseDot} style={{ background: color }} />
      {label}
      {secs != null && <span className={styles.phaseSecs}>{fmtDuration(secs * 1000)}</span>}
    </span>
  );
}

function ProverStatusBar({ provers }: { provers?: Record<string, { file: string; status: string }> }) {
  if (!provers) return null;
  const entries = Object.values(provers);
  const done = entries.filter(p => p.status === 'done').length;
  const error = entries.filter(p => p.status === 'error').length;
  const running = entries.filter(p => p.status === 'running').length;
  return (
    <div className={styles.proverBar}>
      {done > 0 && <span style={{ color: 'var(--green)' }}>✓{done}</span>}
      {running > 0 && <span style={{ color: 'var(--blue)' }}>●{running}</span>}
      {error > 0 && <span style={{ color: 'var(--red)' }}>✗{error}</span>}
      <span className={styles.proverTotal}>/{entries.length}</span>
    </div>
  );
}

function IterGroup({ group, selectedFile, onSelect }: {
  group: LogGroup;
  selectedFile: string;
  onSelect: (path: string) => void;
}) {
  const hasSelected = group.files.some(f => f.path === selectedFile);
  const [expanded, setExpanded] = useState(hasSelected);
  const meta = group.meta;

  useEffect(() => { if (hasSelected) setExpanded(true); }, [hasSelected]);

  const isComplete = !!meta?.completedAt;

  // Derive current active phase from status fields
  const activePhase = !isComplete && meta
    ? (meta.review?.status === 'running' ? 'review'
      : meta.prover?.status === 'running' ? 'prover'
      : meta.plan?.status === 'running' ? 'plan'
      : meta.stage)
    : undefined;

  return (
    <div className={styles.group}>
      <div className={styles.groupHeader} onClick={() => setExpanded(!expanded)}>
        <span className={styles.toggle}>{expanded ? '▾' : '▸'}</span>
        <span className={styles.groupTitle}>
          {meta?.iteration != null ? `Iter #${meta.iteration}` : group.id}
        </span>
        {meta?.mode === 'parallel' && <span className={styles.groupMode}>∥</span>}
        {isComplete && <span className={styles.groupDone}>✓</span>}
        {!isComplete && activePhase && <span className={styles.groupStage}>{activePhase}</span>}
        {!isComplete && (meta?.prover?.status === 'running' || meta?.plan?.status === 'running' || meta?.review?.status === 'running') && <span className={styles.groupLive}>●</span>}
      </div>

      {expanded && (
        <div className={styles.groupBody}>
          {meta && (
            <div className={styles.metaBar}>
              <PhaseTag label="plan" status={meta.plan?.status} secs={meta.plan?.durationSecs} />
              <PhaseTag label="prover" status={meta.prover?.status} secs={meta.prover?.durationSecs} />
              <PhaseTag label="review" status={meta.review?.status} secs={meta.review?.durationSecs} />
              <ProverStatusBar provers={meta.provers} />
            </div>
          )}

          {group.files.map(f => {
            const isProver = f.role === 'prover' && f.path.includes('/provers/');
            const displayName = isProver
              ? f.name.replace('.jsonl', '').replace(/_/g, '/')
              : f.role || f.name.replace('.jsonl', '');
            if (f.name === 'provers-combined.jsonl') return null;

            const proverSlug = f.name.replace('.jsonl', '');
            const proverStatus = isProver && meta?.provers?.[proverSlug]?.status;

            return (
              <div
                key={f.path}
                className={`${styles.fileItem} ${f.path === selectedFile ? styles.fileItemActive : ''}`}
                onClick={() => onSelect(f.path)}
              >
                {isProver && (
                  <span className={styles.fileStatus} style={{
                    color: proverStatus === 'done' ? 'var(--green)' : proverStatus === 'running' ? 'var(--blue)' : proverStatus === 'error' ? 'var(--red)' : 'var(--text-muted)'
                  }}>●</span>
                )}
                {!isProver && <span className={styles.fileRole}>{f.role}</span>}
                <span className={styles.fileName}>{isProver ? displayName : ''}</span>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

// --- Role tag colors ---
const ROLE_COLORS: Record<string, string> = {
  plan: 'var(--blue)',
  prover: 'var(--purple)',
  review: 'var(--orange)',
};

// --- Run summary bar (from session_end entry) ---
function RunSummaryBar({ entries }: { entries: LogEntry[] }) {
  const sessionEnd = entries.find(e => e.event === 'session_end');
  if (!sessionEnd) return null;
  const model = sessionEnd.model_usage ? Object.keys(sessionEnd.model_usage)[0]?.replace(/^claude-/, '').replace(/-\d{8}$/, '') : '';
  const parts: string[] = [];
  if (model) parts.push(model);
  if (sessionEnd.duration_ms) parts.push(fmtDuration(sessionEnd.duration_ms));
  if (sessionEnd.num_turns) parts.push(`${sessionEnd.num_turns} turns`);
  if (sessionEnd.total_cost_usd) parts.push(`$${sessionEnd.total_cost_usd.toFixed(2)}`);
  if (sessionEnd.input_tokens) parts.push(`${(sessionEnd.input_tokens / 1000).toFixed(0)}K in`);
  if (sessionEnd.output_tokens) parts.push(`${(sessionEnd.output_tokens / 1000).toFixed(0)}K out`);
  if (!parts.length) return null;
  return <div className={styles.sessionSummary}>{parts.join(' · ')}</div>;
}

// --- Main LogViewer ---

export default function LogViewer() {
  const [selectedFile, setSelectedFile] = useState('');
  const [filter, setFilter] = useState('all');

  const { data: logsData } = useLogs();
  const { entries, streaming } = useLogStream(selectedFile);

  // Derive the role of the selected file
  const selectedRole = useMemo(() => {
    if (!logsData || !selectedFile) return '';
    for (const g of logsData.groups) {
      const f = g.files.find(f => f.path === selectedFile);
      if (f?.role) return f.role;
    }
    return '';
  }, [logsData, selectedFile]);

  // Auto-select first file
  useEffect(() => {
    if (!logsData || selectedFile) return;
    if (logsData.groups.length > 0) {
      const lastGroup = logsData.groups[logsData.groups.length - 1];
      if (lastGroup.files.length > 0) { setSelectedFile(lastGroup.files[0].path); return; }
    }
    if (logsData.flat.length > 0) setSelectedFile(logsData.flat[0].path);
  }, [logsData, selectedFile]);

  // Filtered entries
  const filtered = useMemo(() => {
    if (filter === 'all') return entries;
    return entries.filter(e => e.event === filter);
  }, [entries, filter]);

  // Summary from session_end (shown at top)
  const sessionEnd = useMemo(() => entries.find(e => e.event === 'session_end'), [entries]);

  const selectedLabel = selectedFile.replace(/\.jsonl$/, '').replace(/\//g, ' / ');

  return (
    <div className={styles.root}>
      {/* Sidebar */}
      <div className={styles.sidebar}>
        {logsData?.groups.slice().reverse().map(g => (
          <IterGroup key={g.id} group={g} selectedFile={selectedFile} onSelect={setSelectedFile} />
        ))}

        {logsData?.flat && logsData.flat.length > 0 && (
          <div className={styles.group}>
            <div className={styles.groupHeader}>
              <span className={styles.groupTitle}>Legacy logs</span>
            </div>
            <div className={styles.groupBody}>
              {logsData.flat.map(f => (
                <div
                  key={f.path}
                  className={`${styles.fileItem} ${f.path === selectedFile ? styles.fileItemActive : ''}`}
                  onClick={() => setSelectedFile(f.path)}
                >
                  <span className={styles.fileName}>{f.name}</span>
                </div>
              ))}
            </div>
          </div>
        )}

        {!logsData?.groups.length && !logsData?.flat.length && (
          <div className={styles.emptyHint}>No logs yet</div>
        )}
      </div>

      {/* Main content */}
      <div className={styles.main}>
        <div className={styles.toolbar}>
          {selectedRole && (
            <span className={styles.roleTag} style={{ color: ROLE_COLORS[selectedRole] || 'var(--text-muted)' }}>
              {selectedRole}
            </span>
          )}
          <span className={styles.selectedLabel}>{selectedLabel || 'Select a log'}</span>
          <select value={filter} onChange={e => setFilter(e.target.value)}>
            <option value="all">All</option>
            <option value="shell">shell</option>
            <option value="thinking">thinking</option>
            <option value="tool_call">tool_call</option>
            <option value="tool_result">tool_result</option>
            <option value="text">text</option>
            <option value="session_end">session_end</option>
          </select>
          {streaming && <span className={styles.live}>● live</span>}
          <span className={styles.count}>{filtered.length} entries</span>
        </div>

        <RunSummaryBar entries={entries} />

        <div className={styles.container}>
          {/* Summary block at top */}
          {sessionEnd?.summary && (
            <div className={styles.summaryBlock}>
              <span className={styles.summaryLabel}>Summary</span>
              <span className={styles.summaryText}>{sessionEnd.summary}</span>
            </div>
          )}

          {/* All entries, newest first */}
          {filtered.filter(e => e.event !== 'session_end').slice().reverse().map((e, i) => (
            <LogEntryLine key={`${entries.length}-${i}`} entry={e} />
          ))}

          {entries.length === 0 && selectedFile && (
            <div className={styles.emptyContent}>No entries in this log file yet.</div>
          )}
        </div>
      </div>
    </div>
  );
}
