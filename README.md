# Speedrift

Speedrift is a small sidecar tool for [workgraph](https://graphwork.github.io/) that reduces agent/task drift without hard-blocking development.

It does 3 things:
1. Stores a tiny, machine-readable **task contract** inside the workgraph task `description`.
2. Computes an advisory **drift report** from `git diff` (scope drift, dependency drift, churn, "hardening"/fallback creep).
3. Automatically **logs findings** back into workgraph and can **create follow-up tasks** (for example `harden:` tasks) instead of letting the current task silently bloat.

## Concept (Motorsports)

Speedrift treats drift like motorsports drifting: you don't ban drift, you keep it **controlled at speed**.

- **Contract**: the intended racing line (objective, non-goals, touch set, budgets).
- **Telemetry**: drift score + findings from actual diffs.
- **Countersteer**: actionable recommendations when drift appears.
- **Pit stop**: if drift persists over multiple checks, Speedrift can spawn a `pit-stop:` task to re-sync.

## Why `description` (not JSON fields)?

Today, `wg` drops unknown JSON fields when it rewrites tasks (for example on `wg claim` / `wg log`). That makes it unsafe to store contracts as custom task fields.

The `description` field survives all state transitions, so we embed a fenced TOML block there.

## Contract Format

Add this at the top of a task description:

````md
```wg-contract
schema = 1
mode = "core"  # core | harden | perf | explore
objective = "…"
non_goals = [
  "No fallbacks/retries/guardrails unless acceptance requires it",
]
touch = [
  "src/**",
]
acceptance = [
  "python3 -m unittest",
]
max_files = 25
max_loc = 800
pit_stop_after = 3
auto_followups = true
```
````

Notes:
- `touch` is repo-root-relative globs. Use `**` explicitly (example: `**/*.md`).
- When `touch` is empty, scope drift checks are skipped.

## Usage

From a repo that has `.workgraph/graph.jsonl`:

```bash
# Show drift report for the only in-progress task (or pass --task <id>)
./bin/speedrift check

# Scan all in-progress tasks and wg-log findings; optionally create followups
./bin/speedrift scan --write-log --create-followups

# Continuous mode (useful as a background sidecar)
./bin/speedrift watch --write-log --create-followups --interval 30

# Orchestrated mode (two agents, parallel)
./bin/speedrift orchestrate --write-log --create-followups --interval 30 --redirect-interval 5

# Inspect or edit contracts (edits graph.jsonl)
./bin/speedrift contract show --task <id>
./bin/speedrift contract set-touch --task <id> src/** tests/**

# Ensure every open/in-progress task has a default contract block (edits graph.jsonl)
./bin/speedrift ensure-contracts --apply
```

`./bin/wg-drift` is kept as a compatibility alias.

## Testing

```bash
python3 -m unittest discover -s tests -p 'test_*.py'
scripts/e2e_smoke.sh
```

## State

Speedrift stores drift history in `.workgraph/.speedrift/state.json` (ignored by drift checks).

It can also run in a split-agent mode:
- `speedrift monitor`: telemetry agent that appends drift reports to `.workgraph/.speedrift/events.jsonl`
- `speedrift redirect`: redirect agent that consumes events and applies actions (`wg log`, follow-ups, pit-stops)

## Roadmap / Upstream PR Ideas

If you want this to be first-class in `wg`, the clean upstream changes are:
- Preserve unknown JSON fields (or add explicit `metadata`) so contracts can be structured fields.
- Add `wg task edit` / `wg task set-description` to avoid direct `graph.jsonl` edits.
- Add `wg drift` as an official command that emits drift reports and optionally spawns follow-up tasks.
