# Archon UI

Web dashboard for monitoring Archon formalization runs — browse iteration logs in real time, track parallel prover status, and review proof journal milestones across sessions.

## Quick Start

```bash
# From the Archon root directory:
bash ui/start.sh --project /path/to/your-lean-project

# With options:
bash ui/start.sh --project workspace/my-project --port 9090 --open
```

`start.sh` handles everything: checks dependencies, installs npm packages, builds the client, and starts the server. Run it again to restart — it auto-kills the previous instance.

## Views

| View | Path | What it shows |
|------|------|---------------|
| **Overview** | `/` | Current stage, sorry count, tasks, cost summary |
| **Logs** | `/logs` | Iteration-grouped log browser with real-time streaming |
| **Journal** | `/journal` | Proof milestones per session, cross-session target aggregation |

The **Logs** view is the primary interface. The left sidebar organizes logs by iteration, showing phase status (plan → prover → review) and per-prover completion. Selecting any log file shows the full agent output with event filtering and live WebSocket streaming. New entries appear at the top.

The **Journal** view has two tabs:
- **Milestones** — per-session: milestones, summary, and recommendations from the review agent
- **Targets** — cross-session: each theorem's status evolution across all sessions, with full attempt history

## Architecture

```
ui/
├── start.sh                        # Launcher (dependency check, build, serve)
├── README.md
├── package.json                    # Workspace-level scripts
│
├── server/                         # Fastify backend (TypeScript, ESM)
│   ├── src/
│   │   ├── index.ts                # Server entry — composes route modules
│   │   ├── types.ts                # Shared type definitions (LogEntry, etc.)
│   │   ├── utils.ts                # readFileOr, parseJsonl
│   │   └── routes/
│   │       ├── project.ts          # /api/project, /api/progress, /api/tasks, /api/sorry-count
│   │       ├── logs.ts             # /api/logs (tree), /api/logs/* (content), /api/log-stream/* (ws)
│   │       ├── iterations.ts       # /api/iterations, /api/iterations/:id, .../provers/:file
│   │       ├── journal.ts          # /api/journal/sessions, milestones, all-milestones
│   │       └── summary.ts          # /api/summary (aggregated cost/token stats)
│   ├── package.json
│   └── tsconfig.json
│
└── client/                         # React SPA (Vite + TypeScript)
    ├── src/
    │   ├── App.tsx                 # Router + connection error banner
    │   ├── views/                   # Each view has a co-located .module.css
    │   │   ├── Overview.tsx        # Stage progress, sorry count, tasks
    │   │   ├── LogViewer.tsx       # Iteration sidebar + flat log viewer
    │   │   └── Journal.tsx         # Milestones (per-session) + Targets (cross-session)
    │   ├── components/             # Each component has a co-located .module.css
    │   │   ├── LogEntryLine.tsx    # Single log entry (text, tool_call, etc.)
    │   │   ├── MilestoneCard.tsx   # Journal milestone display
    │   │   ├── AttemptCard.tsx     # Proof attempt detail
    │   │   └── MarkdownBlock.tsx   # Lightweight markdown renderer
    │   ├── hooks/
    │   │   ├── useApi.ts           # React Query hooks for all REST endpoints
    │   │   └── useLogStream.ts     # WebSocket streaming + REST poll fallback
    │   ├── types/index.ts          # Client-side type definitions
    │   ├── utils/
    │   │   ├── format.ts           # Duration, number formatting
    │   │   ├── aggregate.ts        # Cross-session target aggregation
    │   │   ├── markdown.ts         # Markdown → HTML conversion
    │   │   └── constants.ts        # Status colors, shared constants
    │   └── styles/global.css       # CSS variables, base styles
    ├── package.json
    └── tsconfig.json
```

## Data Sources

The server reads directly from the project's `.archon/` directory:

```
.archon/
├── PROGRESS.md                     # Stage + objectives (Overview)
├── PROJECT_STATUS.md               # Project status summary
├── task_pending.md / task_done.md  # Task lists (Overview)
├── logs/
│   ├── iter-001/                   # Iteration directories
│   │   ├── meta.json               # Phase status, timing, prover states
│   │   ├── plan.jsonl              # Plan agent log
│   │   ├── prover.jsonl            # Prover log (serial mode)
│   │   ├── provers/                # Parallel prover logs
│   │   │   ├── File_A.jsonl
│   │   │   └── ...
│   │   └── review.jsonl            # Review agent log
│   └── iter-002/
│       └── ...
└── proof-journal/
    └── sessions/
        └── session_1/
            ├── milestones.jsonl    # Structured proof attempt data
            ├── summary.md          # Session summary
            └── recommendations.md  # Next steps
```

Both the serial layout (`iter-NNN/prover.jsonl`) and parallel layout (`iter-NNN/provers/*.jsonl`) are supported.

## API Reference

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/project` | GET | Project name and paths |
| `/api/progress` | GET | Current stage, objectives, checklist |
| `/api/tasks` | GET | Pending and completed tasks |
| `/api/sorry-count` | GET | Sorry count across .lean files |
| `/api/logs` | GET | Tree-structured log listing (`{ flat, groups }`) |
| `/api/logs/*` | GET | Parse a specific .jsonl file |
| `/api/log-stream/*` | WS | Real-time log streaming via WebSocket |
| `/api/iterations` | GET | All iteration summaries from meta.json |
| `/api/iterations/:id` | GET | Single iteration detail + prover file list |
| `/api/iterations/:id/provers/:file` | GET | Single prover log entries |
| `/api/journal/sessions` | GET | List sessions with content availability flags |
| `/api/journal/sessions/:id/milestones` | GET | Proof milestones for a session |
| `/api/journal/sessions/:id/summary` | GET | Session summary (markdown) |
| `/api/journal/sessions/:id/recommendations` | GET | Recommendations (markdown) |
| `/api/journal/all-milestones` | GET | All milestones across all sessions (for cross-session aggregation) |
| `/api/journal/status` | GET | PROJECT_STATUS.md content |
| `/api/summary` | GET | Aggregated cost, tokens, duration across all logs |

## start.sh Options

```
bash ui/start.sh --project PATH [OPTIONS]

--project PATH    Lean project path (required, must contain .archon/)
--port PORT       Server port (default: 8080)
--dev             Dev mode: tsx watch + vite dev server on :5173
--build           Build client only, don't start server
--open            Open browser after starting
-h, --help        Show help
```

Port detection works on macOS (`lsof`), Linux (`ss`), and minimal containers (`/proc/net/tcp`).
