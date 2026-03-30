# coredrift

Baseline drift lane — always runs. Scope/contract checking for every task.

<!-- driftdriver-claude:start -->
## Speedrift Ecosystem

**Speedrift** is the development quality system across this workspace. It combines
[Workgraph](https://github.com/graphwork/workgraph) (task spine) with
[Driftdriver](https://github.com/dbmcco/driftdriver) (drift orchestrator) to keep
code, specs, and intent in sync without hard-blocking work.

Use `/speedrift` (or `/rifts`) to invoke the full protocol skill.

### Quick Reference

```bash
# Drift-check a task (run at start + before completion)
./.workgraph/drifts check --task <id> --write-log --create-followups

# Ecosystem dashboard (40+ repos, pressure scores, action queue)
# Local:     http://127.0.0.1:8777/
# Tailscale: http://100.77.214.44:8777/

# Create tasks with current wg flags
wg add "Title" --after <dep-id> --immediate --verify "test command"

# Attractor loop — check convergence status or run convergence
driftdriver attractor status --json
driftdriver attractor run --json
```

### Execution Layer (wg + Agency)
- **Workgraph** is the task spine: dependencies, dispatch, readiness.
- **Agency** (`agency serve`, port 8000) is the agent composition engine: *who* runs a task.
  At dispatch time Agency composes an agent configuration; planforge/speedrift wrap it with
  the protocol envelope (wg-contract, drift checks, executor guidance).
- Agency is always-on launchd. If unreachable, dispatch continues with generic prompts.
- Check: `curl -s http://localhost:8000/health`

### Runtime Authority
- Workgraph is the task/dependency source of truth. `speedriftd` is the repo-local supervisor.
- Sessions default to `observe`. Do not use `wg service start` as a generic kickoff.
- Refresh state: `driftdriver --dir "$PWD" --json speedriftd status --refresh`
- Arm repo: `driftdriver --dir "$PWD" speedriftd status --set-mode supervise --lease-owner <agent> --reason "reason"`
- Disarm: `driftdriver --dir "$PWD" speedriftd status --set-mode observe --release-lease --reason "done"`

### Dark Factory
This repo is part of a dark factory managed by the **Factory Brain** — a three-tier
LLM supervisor (Haiku → Sonnet → Opus) that watches all enrolled repos via events
and heartbeats.

**What the brain does:**
- Monitors `events.jsonl` for lifecycle events (crashes, stalls, agent deaths)
- Checks dispatch-loop heartbeats for stale repos
- Issues directives: restart loops, kill daemons, spawn agents, adjust concurrency, enroll/unenroll repos
- Escalates through tiers when lower tiers can't resolve issues
- Sends Telegram alerts for significant events

**How interactive sessions coexist:**
- When you open a Claude Code session, a `session.started` event is emitted and
  interactive presence is registered automatically (via hooks).
- The brain **suppresses action directives** for repos with active interactive sessions.
- When you close the session, `session.ended` fires and the brain resumes control.
- If a session crashes without clean exit, the brain resumes after the presence
  heartbeat goes stale (~10 minutes).

**You don't need to do anything.** The hooks handle session detection automatically.
The brain backs off when you're here and resumes when you leave.

### Attractor Loop (Convergence Engine)
- Each repo declares a target attractor in `drift-policy.toml`: `onboarded` → `production-ready` → `hardened`
- The loop runs diagnose → plan → execute → re-diagnose until convergence or circuit breaker
- Circuit breakers: max 3 passes, plateau detection (2 consecutive no-improvement), task budget cap (30)
- Bundles (reusable fix templates) are matched to findings automatically; unmatched findings escalate
- Check status: `driftdriver attractor status --json`
- Run convergence: `driftdriver attractor run --json`

### What Happens Automatically
- **Drift task guard**: follow-up tasks are deduped + capped at 3 per lane per repo
- **Attractor convergence**: repos are driven toward their declared target state via the attractor loop
- **Factory brain**: watches events, restarts crashed loops, escalates persistent issues
- **Session awareness**: brain backs off when interactive sessions are active
- **Notifications**: significant findings alert via terminal/webhook/wg-notify
- **Prompt evolution**: recurring drift patterns trigger `wg evolve` to teach agents
- **Outcome learning**: resolution rates feed back into notification significance scoring
<!-- driftdriver-claude:end -->
