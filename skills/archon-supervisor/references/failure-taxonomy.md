# Failure Taxonomy

Use these labels consistently in notes and supervision.

## Fidelity Failures

- `theorem mutation`: the theorem or lemma header differs from `source/`
- `added hypothesis`: new assumptions were injected into the original target
- `changed conclusion`: the statement was weakened or otherwise changed
- `copied state contamination`: `.archon/` history came from another run

## Runtime Failures

- `stale process`: leftover `archon-loop.sh`, `codex exec`, or `lake serve`
- `toolchain lock`: Lean verification blocked by infrastructure contention
- `no progress`: repeated cycles with no Lean-file change and no blocker note

## Mathematical Failures

- `validated blocker`: theorem is false or underspecified as written and the workspace contains a blocker note
- `missing blocker note`: theorem remains unsolved but no actionable explanation was written

Always prefer a clean blocker over a contaminated success.
