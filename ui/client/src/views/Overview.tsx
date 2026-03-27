import { useProgress, useSummary, useSorryCount, useTasks } from '../hooks/useApi';
import { fmtDuration, fmtTime } from '../utils/format';
import { STATUS_COLORS } from '../utils/constants';
import styles from './Overview.module.css';

/** Render inline markdown: **bold** and `code` */
function InlineMd({ text }: { text: string }) {
  const html = text
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    .replace(/`([^`]+)`/g, '<code>$1</code>');
  return <span dangerouslySetInnerHTML={{ __html: html }} />;
}

const STAGES = ['init', 'autoformalize', 'prover', 'polish', 'COMPLETE'];

export default function Overview() {
  const { data: progress } = useProgress();
  const { data: summary } = useSummary();
  const { data: sorryData } = useSorryCount();
  const { data: tasks } = useTasks();

  const stage = progress?.stage || 'init';
  const stageIdx = STAGES.indexOf(stage);

  return (
    <div className={styles.root}>
      <div className={styles.stages}>
        {STAGES.map((s, i) => (
          <span key={s} className={i === stageIdx ? styles.current : i < stageIdx ? styles.done : styles.future}>
            {i < stageIdx ? '✓ ' : ''}{s}
          </span>
        )).reduce<React.ReactNode[]>((acc, el, i) => {
          if (i > 0) acc.push(<span key={`sep-${i}`} className={styles.sep}>→</span>);
          acc.push(el);
          return acc;
        }, [])}
      </div>

      {sorryData && sorryData.total > 0 && (
        <div className={styles.sorrySection}>
          <span className={styles.sorryCount}>{sorryData.total} sorry</span>
          <span className={styles.sorryDetail}>
            {sorryData.files.map(f => `${f.file} (${f.count})`).join(', ')}
          </span>
        </div>
      )}
      {sorryData && sorryData.total === 0 && (
        <div className={styles.sorrySection}>
          <span className={styles.sorryDone}>0 sorry ✓</span>
        </div>
      )}

      {summary && summary.sessionCount > 0 && (
        <div className={styles.summaryLine}>
          {summary.sessionCount} session{summary.sessionCount > 1 ? 's' : ''}
          {' · '}{fmtDuration(summary.totalDuration)}
        </div>
      )}

      {progress?.objectives && progress.objectives.length > 0 && (
        <div className={styles.section}>
          <div className={styles.sectionLabel}>Objectives</div>
          <ul className={styles.list}>
            {progress.objectives.map((o, i) => <li key={i}><InlineMd text={o} /></li>)}
          </ul>
        </div>
      )}

      {tasks && tasks.length > 0 && (
        <div className={styles.section}>
          <div className={styles.sectionLabel}>Tasks</div>
          <div className={styles.taskList}>
            {tasks.map(t => (
              <div key={t.id} className={styles.task}>
                <span className={styles.taskStatus} style={{ color: STATUS_COLORS[t.status] || 'var(--text-muted)' }}>●</span>
                <span className={styles.taskTheorem}>{t.theorem}</span>
                {t.file && <span className={styles.taskFile}>{t.file}</span>}
              </div>
            ))}
          </div>
        </div>
      )}

      {progress?.checklist && progress.checklist.length > 0 && (
        <div className={styles.section}>
          <div className={styles.sectionLabel}>Stages</div>
          <ul className={styles.list}>
            {progress.checklist.map((c, i) => (
              <li key={i} className={c.done ? styles.checkDone : ''}>
                {c.done ? '✓' : '○'} {c.label}
              </li>
            ))}
          </ul>
        </div>
      )}

      {summary?.sessions && summary.sessions.length > 0 && (
        <div className={styles.section}>
          <div className={styles.sectionLabel}>Sessions</div>
          <table className={styles.table}>
            <thead>
              <tr><th>Time</th><th>Model</th><th>Duration</th><th>Turns</th></tr>
            </thead>
            <tbody>
              {summary.sessions.slice().reverse().map((s, i) => (
                <tr key={i}>
                  <td>{fmtTime(s.timestamp)}</td>
                  <td>{s.model}</td>
                  <td>{fmtDuration(s.duration)}</td>
                  <td>{s.turns}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
