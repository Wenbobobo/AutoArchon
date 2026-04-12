import { useState, useEffect, useMemo, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import { useLogs } from '../hooks/useApi';
import { useLogDeepLink } from '../hooks/useLogDeepLink';
import { useLogStream } from '../hooks/useLogStream';
import type { LogEntry, LogGroup } from '../types';
import { fmtDuration } from '../utils/format';
import LogEntryLine from '../components/LogEntryLine';
import MarkdownBlock from '../components/MarkdownBlock';
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

function fmtElapsedMinutes(startedAt?: string, nowMs?: number): string {
  if (!startedAt || nowMs == null) return '';
  const startedMs = new Date(startedAt).getTime();
  if (Number.isNaN(startedMs)) return '';
  const elapsedMs = Math.max(0, nowMs - startedMs);
  const elapsedMin = elapsedMs / 60000;
  if (elapsedMin < 1) return '<1 min';
  return `${Math.floor(elapsedMin)} min`;
}

function IterGroup({ group, selectedFile, onSelect, isLatest, nowMs }: {
  group: LogGroup;
  selectedFile: string;
  onSelect: (path: string) => void;
  isLatest: boolean;
  nowMs: number;
}) {
  const hasSelected = group.files.some(f => f.path === selectedFile);
  const [expanded, setExpanded] = useState(hasSelected);
  const meta = group.meta;

  useEffect(() => { if (hasSelected) setExpanded(true); }, [hasSelected]);

  const isComplete = !!meta?.completedAt;
  const canShowRunning = isLatest && !isComplete;
  const runningElapsed = canShowRunning
    && (meta?.prover?.status === 'running' || meta?.plan?.status === 'running' || meta?.review?.status === 'running')
    ? fmtElapsedMinutes(meta?.startedAt, nowMs)
    : '';

  // Only the latest iteration may present a running/live state.
  const activePhase = canShowRunning && meta
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
        {canShowRunning && activePhase && <span className={styles.groupStage}>{activePhase}</span>}
        {canShowRunning && (meta?.prover?.status === 'running' || meta?.plan?.status === 'running' || meta?.review?.status === 'running') && <span className={styles.groupLive}>●</span>}
        {runningElapsed && <span className={styles.groupElapsed}>{runningElapsed}</span>}
      </div>

      {expanded && (
        <div className={styles.groupBody}>
          {meta && (
            <div className={styles.metaBar}>
              <PhaseTag label="plan" status={canShowRunning ? meta.plan?.status : (meta.plan?.status === 'done' ? 'done' : undefined)} secs={meta.plan?.durationSecs} />
              <PhaseTag label="prover" status={canShowRunning ? meta.prover?.status : (meta.prover?.status === 'done' ? 'done' : undefined)} secs={meta.prover?.durationSecs} />
              <PhaseTag label="review" status={canShowRunning ? meta.review?.status : (meta.review?.status === 'done' ? 'done' : undefined)} secs={meta.review?.durationSecs} />
              <ProverStatusBar provers={canShowRunning ? meta.provers : Object.fromEntries(Object.entries(meta.provers || {}).map(([k, v]) => [k, { ...v, status: v.status === 'done' ? 'done' : 'stale' }]))} />
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

const FILTER_OPTIONS = [
  { value: 'shell', label: 'shell' },
  { value: 'thinking', label: 'thinking' },
  { value: 'tool_call', label: 'tool call' },
  { value: 'tool_result', label: 'tool result' },
  { value: 'text', label: 'text' },
  { value: 'code_snapshot', label: 'snapshot' },
  { value: 'session_end', label: 'session end' },
] as const;

type FilterEvent = typeof FILTER_OPTIONS[number]['value'];

const DEFAULT_FILTERS: FilterEvent[] = FILTER_OPTIONS.map(option => option.value);

// --- Run summary bar (from session_end entry) ---
function RunSummaryBar({ entries }: { entries: LogEntry[] }) {
  const sessionEnd = entries.find(e => e.event === 'session_end');
  if (!sessionEnd) return null;
  const model = sessionEnd.model_usage ? Object.keys(sessionEnd.model_usage)[0]?.replace(/-\d{8}$/, '') : '';
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
  const [selectedFilters, setSelectedFilters] = useState<FilterEvent[]>(DEFAULT_FILTERS);
  const [nowMs, setNowMs] = useState(() => Date.now());
  const navigate = useNavigate();
  const highlightRef = useRef<HTMLDivElement>(null);

  const { data: logsData } = useLogs();
  const { initialSelectedFile, initialHighlightTs, backTarget } = useLogDeepLink(logsData);
  const { entries, streaming } = useLogStream(selectedFile);
  const highlightConsumedRef = useRef(false);

  const goBackToDiffs = () => {
    if (!backTarget) return;
    navigate(`${backTarget.pathname}${backTarget.search || ''}`);
  };

  const toggleFilter = (event: FilterEvent) => {
    setSelectedFilters(current => (
      current.includes(event)
        ? current.filter(value => value !== event)
        : [...current, event]
    ));
  };

  const resetFilters = () => {
    setSelectedFilters(DEFAULT_FILTERS);
  };

  const allFiltersSelected = selectedFilters.length === DEFAULT_FILTERS.length;
  const selectedFilterSet = useMemo(() => new Set<FilterEvent>(selectedFilters), [selectedFilters]);

  useEffect(() => {
    const interval = window.setInterval(() => setNowMs(Date.now()), 30000);
    return () => window.clearInterval(interval);
  }, []);

  // Consume deep-link once on first load
  useEffect(() => {
    if (!selectedFile && initialSelectedFile) {
      setSelectedFile(initialSelectedFile);
    }
  }, [selectedFile, initialSelectedFile]);

  // Scroll to highlighted timestamp only once, on the initial deep-linked file
  useEffect(() => {
    if (highlightConsumedRef.current) return;
    if (!selectedFile || !initialSelectedFile || selectedFile !== initialSelectedFile) return;
    if (highlightRef.current) {
      highlightRef.current.scrollIntoView({ behavior: 'smooth', block: 'center' });
      highlightConsumedRef.current = true;
    }
  }, [selectedFile, initialSelectedFile, entries]);

  // Pre-compute closest entry ts only for the initial deep-linked file
  const closestHighlightTs = useMemo(() => {
    if (selectedFile !== initialSelectedFile) return '';
    if (!initialHighlightTs || entries.length === 0) return '';
    const targetTime = new Date(initialHighlightTs).getTime();
    if (isNaN(targetTime)) return '';
    let minDist = Infinity;
    let bestTs = '';
    for (const e of entries) {
      if (!e.ts || e.event === 'session_end') continue;
      const dist = Math.abs(new Date(e.ts).getTime() - targetTime);
      if (dist < minDist) { minDist = dist; bestTs = e.ts; }
    }
    return bestTs;
  }, [entries, initialHighlightTs]);

  // Derive the role of the selected file
  const selectedRole = useMemo(() => {
    if (!logsData || !selectedFile) return '';
    for (const g of logsData.groups) {
      const f = g.files.find(f => f.path === selectedFile);
      if (f?.role) return f.role;
    }
    return '';
  }, [logsData, selectedFile]);

  // Auto-select first file only if deep-link didn't choose one
  useEffect(() => {
    if (!logsData || selectedFile || initialSelectedFile) return;
    if (logsData.groups.length > 0) {
      const lastGroup = logsData.groups[logsData.groups.length - 1];
      if (lastGroup.files.length > 0) { setSelectedFile(lastGroup.files[0].path); return; }
    }
    if (logsData.flat.length > 0) setSelectedFile(logsData.flat[0].path);
  }, [logsData, selectedFile, initialSelectedFile]);

  // Filtered entries
  const filtered = useMemo(() => {
    return entries.filter(e => selectedFilterSet.has(e.event as FilterEvent));
  }, [entries, selectedFilterSet]);

  const visibleEntries = useMemo(() => filtered.filter(e => e.event !== 'session_end'), [filtered]);

  // Summary from session_end (shown at top when selected)
  const sessionEnd = useMemo(() => entries.find(e => e.event === 'session_end'), [entries]);
  const showSessionSummary = !!sessionEnd && selectedFilterSet.has('session_end');

  const selectedLabel = selectedFile.replace(/\.jsonl$/, '').replace(/\//g, ' / ');
  const latestGroupId = useMemo(() => {
    if (!logsData?.groups?.length) return '';
    return logsData.groups.reduce((latest, group) => {
      const latestIter = latest.meta?.iteration ?? Number.NEGATIVE_INFINITY;
      const groupIter = group.meta?.iteration ?? Number.NEGATIVE_INFINITY;
      if (groupIter > latestIter) return group;
      if (groupIter === latestIter && group.id > latest.id) return group;
      return latest;
    }).id;
  }, [logsData]);

  return (
    <div className={styles.root}>
      {/* Sidebar */}
      <div className={styles.sidebar}>
        {logsData?.groups.slice().reverse().map(g => (
          <IterGroup
            key={g.id}
            group={g}
            selectedFile={selectedFile}
            onSelect={setSelectedFile}
            isLatest={g.id === latestGroupId}
            nowMs={nowMs}
          />
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
          {backTarget && (
            <button className={styles.backBtn} onClick={goBackToDiffs} title="Back to Diffs view">
              ← Diffs
            </button>
          )}
          {selectedRole && (
            <span className={styles.roleTag} style={{ color: ROLE_COLORS[selectedRole] || 'var(--text-muted)' }}>
              {selectedRole}
            </span>
          )}
          <span className={styles.selectedLabel}>{selectedLabel || 'Select a log'}</span>
          <div className={styles.filterBar} aria-label="Event type filters">
            <span className={styles.filterLabel}>Show</span>
            <div className={styles.filterChips}>
              {FILTER_OPTIONS.map(option => {
                const active = selectedFilterSet.has(option.value);
                return (
                  <button
                    key={option.value}
                    type="button"
                    className={`${styles.filterChip} ${active ? styles.filterChipActive : ''}`}
                    onClick={() => toggleFilter(option.value)}
                    aria-pressed={active}
                  >
                    {option.label}
                  </button>
                );
              })}
            </div>
            {!allFiltersSelected && (
              <button type="button" className={styles.resetFiltersBtn} onClick={resetFilters}>
                Reset
              </button>
            )}
          </div>
          {streaming && <span className={styles.live}>● live</span>}
          <span className={styles.count}>{filtered.length} entries</span>
        </div>

        {showSessionSummary && <RunSummaryBar entries={entries} />}

        <div className={styles.container}>
          {/* Summary block at top */}
          {showSessionSummary && sessionEnd?.summary && (
            <div className={styles.summaryBlock}>
              <span className={styles.summaryLabel}>Summary</span>
              <MarkdownBlock content={sessionEnd.summary} className={styles.summaryText} />
            </div>
          )}

          {/* All entries, newest first */}
          {(() => {
            let highlightAttached = false;
            return visibleEntries.slice().reverse().map((e, i) => {
              const isHighlighted = !!(closestHighlightTs && e.ts === closestHighlightTs);
              const attachRef = isHighlighted && !highlightAttached;
              if (attachRef) highlightAttached = true;
              return (
                <div key={e.ts ? `${e.ts}-${e.event}-${i}` : `entry-${i}`}
                     ref={attachRef ? highlightRef : undefined}
                     style={isHighlighted ? { background: 'rgba(3,102,214,0.08)', borderLeft: '3px solid var(--blue)' } : undefined}>
                  <LogEntryLine entry={e} />
                </div>
              );
            });
          })()}

          {selectedFile && filtered.length === 0 && (
            <div className={styles.emptyContent}>No entries match the current filters.</div>
          )}

          {entries.length === 0 && selectedFile && (
            <div className={styles.emptyContent}>No entries in this log file yet.</div>
          )}
        </div>
      </div>
    </div>
  );
}
