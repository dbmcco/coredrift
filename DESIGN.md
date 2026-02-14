# Speedrift Design

## Goals

- Reduce **agent drift** (scope drift, fallback/guardrail creep, churn, dependency creep) without hard-blocking development.
- Keep everything **workgraph-native**: contracts, follow-ups, and escalation are expressed as tasks/logs.
- Stay **polyglot**: drift detection is mostly language-agnostic (diff + paths), and verification is opt-in via contract `acceptance`.

## Non-Goals

- No mandatory gates: Speedrift is advisory by default.
- No full semantic correctness checks: detection is heuristic and biased toward low false-negative drift categories.

## Core Objects

### Task Contract

Stored in the task `description` as a fenced TOML block:

```wg-contract
schema = 1
mode = "core"  # core | harden | perf | explore
objective = "..."
non_goals = ["..."]
touch = ["src/**"]
acceptance = []
max_files = 25
max_loc = 800
pit_stop_after = 3
auto_followups = true
```

Why `description`:
- `wg` currently drops unknown JSON fields during task rewrites, so `description` is the only durable storage.

### Drift Report

Computed from:
- contract (if present)
- current git working state (staged/unstaged/untracked) excluding `.workgraph/` and `.git/`

Outputs:
- `score`: `green|yellow|red`
- `findings`: structured drift categories
- `telemetry`: cheap numeric signals (files changed, loc changed, etc.)
- `recommendations`: "countersteer" actions

### Drift State

Persisted in `.workgraph/.speedrift/state.json`:
- last score/kinds
- drift streak counter
- whether a pit-stop escalation has been created

This lets Speedrift escalate based on **persistence** (not just a single snapshot).

## Behaviors

### Telemetry and Countersteer

When drift is detected, Speedrift emits recommendations like:
- "revert out-of-scope changes or expand touch globs"
- "move guardrails into a `harden:` follow-up task"

### Orchestrated Agents (Monitor + Redirect)

Speedrift supports an “orchestrated” two-agent loop:
- **monitor**: computes drift reports and appends JSONL events to `.workgraph/.speedrift/events.jsonl`
- **redirect**: consumes events, updates drift state, and applies actions (`wg log`, follow-ups, pit-stops)

This keeps monitoring lightweight and lets redirect actions run independently (parallelism and responsibility isolation).

### Follow-Up Tasks (No Hard Blocks)

With `--create-followups`, Speedrift creates deterministic tasks:
- `drift-harden-<task>` (blocked by origin)
- `drift-scope-<task>` (blocked by origin)

These convert “agent anxiety” into explicit downstream work.

### Pit Stop Escalation

If a core task stays yellow for `pit_stop_after` consecutive checks, Speedrift can spawn:
- `speedrift-pit-<task>` (`pit-stop: <task title>`) blocked by origin

This is meant to “drag the project back in sync” without blocking the current task mid-flight.

## Workgraph Integration Points

- `wg log`: Speedrift writes one-line summaries back onto tasks.
- `wg add`: Speedrift creates follow-up tasks as additional graph nodes.
- `graph.jsonl` rewriting: Speedrift edits task descriptions for contract injection and `contract set-touch`.

## Upstream Improvements

If upstream adds either:
- a `metadata` field preserved across rewrites, or
- unknown-field preservation,

then contracts can be stored as structured task data instead of encoding/rewriting `description`.
