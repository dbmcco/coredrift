from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from wg_drift.contracts import extract_contract, format_default_contract_block


@dataclass(frozen=True)
class Workgraph:
    wg_dir: Path
    project_dir: Path
    tasks: dict[str, dict[str, Any]]

    def wg_log(self, task_id: str, message: str) -> None:
        subprocess.check_call(["wg", "--dir", str(self.wg_dir), "log", task_id, message])

    def ensure_task(
        self,
        *,
        task_id: str,
        title: str,
        description: str,
        blocked_by: list[str] | None = None,
        tags: list[str] | None = None,
    ) -> None:
        if task_id in self.tasks:
            return

        cmd = ["wg", "--dir", str(self.wg_dir), "add", title, "--id", task_id]
        if description:
            cmd += ["-d", description]
        if blocked_by:
            cmd += ["--blocked-by", *blocked_by]
        if tags:
            for t in tags:
                cmd += ["-t", t]
        subprocess.check_call(cmd)
        # Keep in-memory index in sync so repeated ensure_task calls stay idempotent.
        self.tasks[task_id] = {"kind": "task", "id": task_id, "title": title}


def find_workgraph_dir(explicit: Path | None) -> Path:
    if explicit:
        p = explicit
        if p.name != ".workgraph":
            p = p / ".workgraph"
        if not (p / "graph.jsonl").exists():
            raise FileNotFoundError(f"Workgraph not found at: {p}")
        return p

    cur = Path.cwd()
    for p in [cur, *cur.parents]:
        candidate = p / ".workgraph" / "graph.jsonl"
        if candidate.exists():
            return candidate.parent
    raise FileNotFoundError("Could not find .workgraph/graph.jsonl; pass --dir.")


def load_workgraph(wg_dir: Path) -> Workgraph:
    graph_path = wg_dir / "graph.jsonl"
    tasks: dict[str, dict[str, Any]] = {}
    for line in graph_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        obj = json.loads(line)
        if obj.get("kind") != "task":
            continue
        tid = str(obj.get("id"))
        tasks[tid] = obj

    return Workgraph(wg_dir=wg_dir, project_dir=wg_dir.parent, tasks=tasks)


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
