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

### One-Time Setup (Per Workgraph Repo)

From the repo you want to run Speedrift in (Speedrift will run `wg init` if needed):

```bash
/Users/braydon/projects/experiments/speedrift/bin/speedrift install
```

Optional (if you also use `uxrift`):

```bash
# Best-effort autodetect (looks at $UXRIFT_BIN, a sibling ../uxrift checkout, or uxrift on PATH)
/Users/braydon/projects/experiments/speedrift/bin/speedrift install --with-uxrift

# Or be explicit:
/Users/braydon/projects/experiments/speedrift/bin/speedrift install --uxrift-bin /path/to/uxrift/bin/uxrift
```

This creates:
- `./.workgraph/speedrift` (a wrapper pinned to this Speedrift checkout)
- `./.workgraph/.gitignore` entry for `.speedrift/` state
- executor prompt guidance under `./.workgraph/executors/` (so spawned agents know the protocol)
- (optional) `./.workgraph/uxrift` wrapper + `.uxrift/` ignore + executor guidance for `uxrift`

### Start / Resume Protocol (The “How Do Agents Know What To Do?” Part)

When you start a new project (or come back after a break), do this from the repo root:

```bash
# 1) Ensure wrapper + executor guidance are installed (idempotent)
/Users/braydon/projects/experiments/speedrift/bin/speedrift install

# 2) Ensure every open/in-progress task has a default contract (idempotent)
./.workgraph/speedrift ensure-contracts --apply

# 3) Write the current drift snapshot into workgraph + spawn follow-ups (optional but recommended)
./.workgraph/speedrift scan --write-log --create-followups

# 4) Keep a drift sidecar running while work happens
./.workgraph/speedrift orchestrate --write-log --create-followups --interval 30 --redirect-interval 5
```

How this coordinates with Workgraph:
- Speedrift stores the contract in the task `description` (as a `wg-contract` fenced block).
- `speedrift install` patches/creates `.workgraph/executors/*.toml` prompt templates with a **Speedrift Protocol** section.
  That means any agent spawned via those executors sees the “run Speedrift at start/before done” instructions automatically.
- Drift never hard-blocks: Speedrift writes `wg log` entries and spawns follow-up tasks (for example `harden:`) instead.

### Daily Use

From that repo:

```bash
# Show drift report for the only in-progress task (or pass --task <id>)
./.workgraph/speedrift check

# Scan all in-progress tasks and wg-log findings; optionally create followups
./.workgraph/speedrift scan --write-log --create-followups

# Continuous mode (useful as a background sidecar)
./.workgraph/speedrift watch --write-log --create-followups --interval 30

# Orchestrated mode (two agents, parallel)
./.workgraph/speedrift orchestrate --write-log --create-followups --interval 30 --redirect-interval 5

# Inspect or edit contracts (edits graph.jsonl)
./.workgraph/speedrift contract show --task <id>
./.workgraph/speedrift contract set-touch --task <id> src/** tests/**

# Ensure every open/in-progress task has a default contract block (edits graph.jsonl)
./.workgraph/speedrift ensure-contracts --apply
```

In the Speedrift repo itself, `./bin/wg-drift` is kept as a compatibility alias.

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
