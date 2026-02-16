# Upstream PR Notes (workgraph)

Coredrift is a prototype for “task contracts + drift radar” built on top of workgraph.

## Problem: Unknown Task Fields Are Dropped

If you manually add custom fields to a task JSON object in `.workgraph/graph.jsonl`, they are lost on the next `wg claim` / `wg log` / status transition (because tasks are re-serialized from a fixed struct).

That makes it impossible to safely store structured task contracts in-task today.

## Minimal Upstream Change: `metadata` + Preserve Unknown Fields

Add an explicit `metadata` field to the task model:
- Type: map/object (`HashMap<String, serde_json::Value>` or similar)
- Serialization: round-trip without dropping keys
- CLI: allow `wg add --meta key=value` (optional), and `wg task set-meta` / `wg task set-description`

Even better: preserve unknown top-level keys (forward-compat), but `metadata` is a clear home.

## Additional CLI Surface (Nice To Have)

1. `wg task edit` (or `wg update`) to modify:
   - description
   - metadata
   - tags/skills/deliverables/inputs (idempotent)

2. `wg drift` command:
   - reads task contract (from metadata or description)
   - computes drift from git state (advisory by default)
   - optionally writes `wg log` and/or creates follow-up tasks

## Why This Matters

Workgraph already solves coordination. Adding first-class contracts/metadata lets agents stay aligned with:
- objective
- non-goals
- allowed touch set
- acceptance/verification

And drift can be handled by spawning follow-up tasks rather than blocking current work.
