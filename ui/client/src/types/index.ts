// --- Log types ---
export interface LogEntry {
  ts: string;
  event: 'shell' | 'thinking' | 'tool_call' | 'tool_result' | 'text' | 'session_end';
  level?: 'info' | 'warn' | 'error';
  message?: string;
  content?: string;
  tool?: string;
  input?: Record<string, unknown>;
  total_cost_usd?: number;
  duration_ms?: number;
  num_turns?: number;
  input_tokens?: number;
  output_tokens?: number;
  model_usage?: Record<string, { inputTokens: number; outputTokens: number; costUSD: number }>;
  summary?: string;
}

export interface LogFile { name: string; path: string; size: number; modified: string; role?: string }

export interface LogGroup {
  id: string;
  files: LogFile[];
  meta?: {
    iteration?: number;
    stage?: string;
    mode?: string;
    startedAt?: string;
    completedAt?: string;
    wallTimeSecs?: number;
    plan?: { status: string; durationSecs?: number };
    prover?: { status: string; durationSecs?: number };
    review?: { status: string; durationSecs?: number };
    provers?: Record<string, { file: string; status: string }>;
  };
}

export interface LogsResponse {
  flat: LogFile[];
  groups: LogGroup[];
}

// --- Progress types ---
export interface ProgressData {
  stage: string;
  objectives: string[];
  checklist: { label: string; done: boolean }[];
}

// --- Task types ---
export interface Task {
  id: string;
  theorem: string;
  file: string;
  status: 'pending' | 'in-progress' | 'done';
  proofSketch?: string;
}

// --- Session summary types ---
export interface SessionSummary {
  cost: number;
  duration: number;
  tokensIn: number;
  tokensOut: number;
  model: string;
  turns: number;
  timestamp: string;
  summary?: string;
}

export interface AggregatedStats {
  totalCost: number;
  totalDuration: number;
  totalTokensIn: number;
  totalTokensOut: number;
  sessionCount: number;
  sessions: SessionSummary[];
}

// --- Sorry count ---
export interface SorryCount {
  total: number;
  files: { file: string; count: number; lines: number[] }[];
}

// --- Journal types (aligned with cctest-dashboard Milestone/Attempt schema) ---
export interface Attempt {
  attempt: number;
  strategy: string;
  code_tried?: string;
  lean_error?: string;
  goal_before?: string;
  goal_after?: string;
  result: string;
  duration_min?: number;
  insight?: string;
}

export interface Milestone {
  timestamp: string;
  status: 'solved' | 'partial' | 'blocked' | 'failed_retry' | 'not_started';
  target: { file: string; theorem: string };
  session: { id: string; model: string };
  findings: { blocker?: string; key_lemmas_used?: string[] };
  attempts: Attempt[];
  next_steps?: string;
}

// --- Iteration types ---
export interface ProverMeta {
  file: string;
  status: 'running' | 'done' | 'error';
}

export interface IterationMeta {
  id: string;
  iteration?: number;
  stage?: string;
  mode?: 'parallel' | 'serial';
  startedAt?: string;
  completedAt?: string;
  wallTimeSecs?: number;
  plan?: { status: string; durationSecs?: number };
  prover?: { status: string; durationSecs?: number };
  review?: { status: string; durationSecs?: number };
  provers?: Record<string, ProverMeta>;
  proverFiles?: { slug: string; size: number }[];
}
