# ABOUTME: Coredrift workgraph helpers — SDK base plus coredrift-specific graph rewriters.
# ABOUTME: Re-exports Workgraph, find_workgraph_dir, load_workgraph from SDK.

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from speedrift_lane_sdk.workgraph import (  # noqa: F401
    Workgraph,
    find_workgraph_dir,
    load_workgraph,
)

from wg_drift.contracts import extract_contract, format_default_contract_block


@dataclass(frozen=True)
class ContractPatchResult:
    updated_tasks: list[str]


@dataclass(frozen=True)
class TaskRewriteResult:
    updated: bool


def update_task_description(*, wg_dir: Path, task_id: str, new_description: str) -> TaskRewriteResult:
    graph_path = wg_dir / "graph.jsonl"
    lines_in = graph_path.read_text(encoding="utf-8").splitlines()
    lines_out: list[str] = []
    updated = False

    for line in lines_in:
        if not line.strip():
            continue
        obj = json.loads(line)
        if obj.get("kind") != "task":
            lines_out.append(line)
            continue
        tid = str(obj.get("id"))
        if tid != task_id:
            lines_out.append(line)
            continue
        obj["description"] = new_description
        updated = True
        lines_out.append(json.dumps(obj, separators=(",", ":")))

    if not updated:
        raise ValueError(f"Task not found in graph.jsonl: {task_id}")

    tmp = graph_path.with_suffix(".jsonl.tmp")
    tmp.write_text("\n".join(lines_out) + "\n", encoding="utf-8")
    tmp.replace(graph_path)

    return TaskRewriteResult(updated=True)


def rewrite_graph_with_contracts(*, wg_dir: Path, statuses: set[str], apply: bool) -> ContractPatchResult:
    wg = load_workgraph(wg_dir)
    updated: list[str] = []

    graph_path = wg_dir / "graph.jsonl"
    lines_in = graph_path.read_text(encoding="utf-8").splitlines()
    lines_out: list[str] = []

    for line in lines_in:
        if not line.strip():
            continue
        obj = json.loads(line)
        if obj.get("kind") != "task":
            lines_out.append(line)
            continue

        tid = str(obj.get("id"))
        status = str(obj.get("status") or "")
        if status not in statuses:
            lines_out.append(line)
            continue

        desc = str(obj.get("description") or "")
        if extract_contract(desc) is not None:
            lines_out.append(line)
            continue

        title = str(obj.get("title") or tid)
        contract_block = format_default_contract_block(mode="core", objective=title, touch=[])
        if desc.strip():
            new_desc = contract_block + "\n" + desc
        else:
            new_desc = contract_block
        obj["description"] = new_desc
        updated.append(tid)
        lines_out.append(json.dumps(obj, separators=(",", ":")))

    if apply and updated:
        tmp = graph_path.with_suffix(".jsonl.tmp")
        tmp.write_text("\n".join(lines_out) + "\n", encoding="utf-8")
        tmp.replace(graph_path)

    return ContractPatchResult(updated_tasks=updated)
