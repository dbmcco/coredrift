# Coredrift

Coredrift is a small sidecar tool for [workgraph](https://graphwork.github.io/) that reduces agent/task drift without hard-blocking development.

It does 3 things:
1. Stores a tiny, machine-readable **task contract** inside the workgraph task `description`.
2. Computes an advisory **drift report** from `git diff` (scope drift, dependency drift, churn, "hardening"/fallback creep).
3. Automatically **logs findings** back into workgraph and can **create follow-up tasks** (for example `harden:` tasks) instead of letting the current task silently bloat.

## Ecosystem Map

This project is part of the Speedrift suite for Workgraph-first drift control.

- Suite home: [speedrift-ecosystem](https://github.com/dbmcco/speedrift-ecosystem)
- Spine: [Workgraph](https://graphwork.github.io/)
- Orchestrator: [driftdriver](https://github.com/dbmcco/driftdriver)
- Baseline lane: [coredrift](https://github.com/dbmcco/coredrift)
- Optional lanes: [specdrift](https://github.com/dbmcco/specdrift), [datadrift](https://github.com/dbmcco/datadrift), [depsdrift](https://github.com/dbmcco/depsdrift), [uxdrift](https://github.com/dbmcco/uxdrift), [therapydrift](https://github.com/dbmcco/therapydrift), [yagnidrift](https://github.com/dbmcco/yagnidrift), [redrift](https://github.com/dbmcco/redrift)

## Concept (Motorsports)

Coredrift treats drift like motorsports drifting: you don't ban drift, you keep it **controlled at speed**.

- **Contract**: the intended racing line (objective, non-goals, touch set, budgets).
- **Telemetry**: drift score + findings from actual diffs.
- **Countersteer**: actionable recommendations when drift appears.
- **Pit stop**: if drift persists over multiple checks, Coredrift can spawn a `pit-stop:` task to re-sync.

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

From the repo you want to run Coredrift in (Coredrift will run `wg init` if needed):

```bash
coredrift install
```

If you are using the broader `drifts` suite, prefer the unified installer:

```bash
driftdriver install
```

Optional (if you also use `uxdrift`):

```bash
# Best-effort autodetect (looks at $UXDRIFT_BIN, a sibling ../uxdrift checkout, or uxdrift on PATH)
coredrift install --with-uxdrift

# Or be explicit:
coredrift install --uxdrift-bin /path/to/uxdrift/bin/uxdrift
```

This creates:
- `./.workgraph/coredrift` (a wrapper)
- `./.workgraph/.gitignore` entry for `.coredrift/` state
- executor prompt guidance under `./.workgraph/executors/` (so spawned agents know the protocol)
- (optional) `./.workgraph/uxdrift` wrapper + `.uxdrift/` ignore + executor guidance for `uxdrift`

### Start / Resume Protocol (The “How Do Agents Know What To Do?” Part)

When you start a new project (or come back after a break), do this from the repo root:

```bash
# 1) Ensure wrapper + executor guidance are installed (idempotent)
coredrift install

# 2) Ensure every open/in-progress task has a default contract (idempotent)
./.workgraph/coredrift ensure-contracts --apply

# 3) Write the current drift snapshot into workgraph + spawn follow-ups (optional but recommended)
./.workgraph/coredrift scan --write-log --create-followups

# 4) Keep a drift sidecar running while work happens
./.workgraph/coredrift orchestrate --write-log --create-followups --interval 30 --redirect-interval 5
```

How this coordinates with Workgraph:
- Coredrift stores the contract in the task `description` (as a `wg-contract` fenced block).
- `coredrift install` patches/creates `.workgraph/executors/*.toml` prompt templates with a **Coredrift Protocol** section.
  That means any agent spawned via those executors sees the “run Coredrift at start/before done” instructions automatically.
- Drift never hard-blocks: Coredrift writes `wg log` entries and spawns follow-up tasks (for example `harden:`) instead.

### Daily Use

From that repo:

```bash
# Unified one-command check (via driftdriver: runs coredrift always; runs optional drifts when the task declares specs)
./.workgraph/drifts check --task <id> --write-log --create-followups

# Show drift report for the only in-progress task (or pass --task <id>)
./.workgraph/coredrift check

# Scan all in-progress tasks and wg-log findings; optionally create followups
./.workgraph/coredrift scan --write-log --create-followups

# Continuous mode (useful as a background sidecar)
./.workgraph/coredrift watch --write-log --create-followups --interval 30

# Orchestrated mode (two agents, parallel)
./.workgraph/coredrift orchestrate --write-log --create-followups --interval 30 --redirect-interval 5

# Inspect or edit contracts (edits graph.jsonl)
./.workgraph/coredrift contract show --task <id>
./.workgraph/coredrift contract set-touch --task <id> src/** tests/**

# Ensure every open/in-progress task has a default contract block (edits graph.jsonl)
./.workgraph/coredrift ensure-contracts --apply
```

In the Coredrift repo itself, run `./bin/coredrift` from the checkout root.

## Testing

```bash
python3 -m unittest discover -s tests -p 'test_*.py'
scripts/e2e_smoke.sh
```

## State

Coredrift stores drift history in `.workgraph/.coredrift/state.json` (ignored by drift checks).

It can also run in a split-agent mode:
- `coredrift monitor`: telemetry agent that appends drift reports to `.workgraph/.coredrift/events.jsonl`
- `coredrift redirect`: redirect agent that consumes events and applies actions (`wg log`, follow-ups, pit-stops)

## Roadmap / Upstream PR Ideas

If you want this to be first-class in `wg`, the clean upstream changes are:
- Preserve unknown JSON fields (or add explicit `metadata`) so contracts can be structured fields.
- Add `wg task edit` / `wg task set-description` to avoid direct `graph.jsonl` edits.
- Add `wg drift` as an official command that emits drift reports and optionally spawns follow-up tasks.
