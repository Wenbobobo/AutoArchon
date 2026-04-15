# Startup Brief

The orchestrator is the campaign owner, not a prover.

Default stance:

- one teacher per isolated run root
- one micro-shard per teacher at a time
- prefer relaunch or shrink over widening scope
- accept only exported artifacts backed by validation
- start with intake when the real user objective, success criteria, or benchmark-faithful boundary is not yet explicit

First checks:

1. verify the real user objective, benchmark-faithful vs formalization intent, and success criteria are explicit enough to write `operator-journal.md`
2. verify the benchmark source and warmed `.lake/` cache path
3. verify the `uv run autoarchon-*` control-plane entrypoints are available
4. verify `control/mission-brief.md`, `control/launch-spec.resolved.json`, and `control/operator-journal.md` exist or can be scaffolded safely
5. verify every run has disjoint `runs/<id>/`
6. verify teachers are launched from generated control files, not from ad hoc prompts
7. if the campaign root does not exist but the prompt provides a source root, bootstrap it before launching any teacher
8. if every run is still `queued`, prefer `uv run --directory <repo-root> autoarchon-campaign-recover --campaign-root <campaign-root> --all-recoverable --execute` for the first fan-out instead of hand-launching each script one by one
9. if the user already gave a `Campaign root`, do not inspect other campaigns unless that root is corrupt and you are explicitly debugging why
10. run `uv run --directory <repo-root> autoarchon-validate-launch-contract --campaign-root <campaign-root>` before the watchdog when possible

Status meanings:

- `queued`: run exists but no live work has started
- `running`: recent supervisor or prover activity exists
- `accepted`: accepted proof artifacts close the scoped targets
- `blocked`: accepted blocker notes close the scoped targets
- `unverified`: changed files or task results exist without full validation closure
- `needs_relaunch`: the run has partial state but no active progress and no closed acceptance
- `contaminated`: theorem fidelity or validation rejection makes the run untrustworthy

Launch note:

- `control/teacher-launch-state.json` is the pre-lease in-flight marker
- `workspace/.archon/supervisor/run-lease.json` is the authoritative live supervisor lease after the teacher is inside the run
