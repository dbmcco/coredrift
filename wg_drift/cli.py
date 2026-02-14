from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from wg_drift.contracts import (
    CONTRACT_FENCE_INFO,
    TaskContract,
    DEFAULT_NON_GOALS,
    extract_contract,
    format_default_contract_block,
    parse_contract,
    replace_contract_block,
)
from wg_drift.drift import compute_drift
from wg_drift.events import append_event, events_path, read_events_since
from wg_drift.git_tools import get_git_root, get_working_changes
from wg_drift.install import (
    ensure_executor_guidance,
    ensure_speedrift_gitignore,
    ensure_uxrift_gitignore,
    write_speedrift_wrapper,
    write_uxrift_wrapper,
)
from wg_drift.state import locked_state, mark_pit_stop_created, update_task_state
from wg_drift.workgraph import (
    Workgraph,
    find_workgraph_dir,
    load_workgraph,
    rewrite_graph_with_contracts,
    update_task_description,
)


@dataclass(frozen=True)
class ExitCode:
    ok: int = 0
    usage: int = 2
    drift_found: int = 3
    error: int = 1


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="speedrift", add_help=True)
    p.add_argument("--dir", help="Path to .workgraph directory (default: search upward from cwd)")

    sub = p.add_subparsers(dest="cmd", required=True)

    install = sub.add_parser("install", help="Install Speedrift into a workgraph (wrapper, ignore, executor guidance)")
    install.add_argument("--no-ensure-contracts", action="store_true", help="Do not inject default contracts into tasks")
    install.add_argument(
        "--with-uxrift",
        action="store_true",
        help="Also install uxrift wrapper + executor protocol (best-effort autodetect)",
    )
    install.add_argument("--uxrift-bin", help="Path to uxrift bin/uxrift (enables uxrift integration)")

    check = sub.add_parser("check", help="Compute drift report for a task (defaults to the only in-progress task)")
    check.add_argument("--task", help="Task id")
    check.add_argument("--json", action="store_true", help="Output as JSON")
    check.add_argument("--create-followups", action="store_true", help="Create follow-up tasks for drift findings")
    check.add_argument("--write-log", action="store_true", help="Write a drift summary to wg log")

    scan = sub.add_parser("scan", help="Scan all in-progress tasks")
    scan.add_argument("--json", action="store_true", help="Output as JSON")
    scan.add_argument("--create-followups", action="store_true", help="Create follow-up tasks for drift findings")
    scan.add_argument("--write-log", action="store_true", help="Write a drift summary to wg log")

    watch = sub.add_parser("watch", help="Continuously scan in-progress tasks and emit logs/followups on change")
    watch.add_argument("--interval", type=int, default=30, help="Poll interval seconds (default: 30)")
    watch.add_argument("--create-followups", action="store_true", help="Create follow-up tasks for drift findings")
    watch.add_argument("--write-log", action="store_true", help="Write a drift summary to wg log")
    watch.add_argument("--json", action="store_true", help="Output periodic reports as JSON (stdout)")

    monitor = sub.add_parser("monitor", help="Telemetry agent: emit drift reports to .workgraph/.speedrift/events.jsonl")
    monitor.add_argument("--interval", type=int, default=30, help="Poll interval seconds (default: 30)")
    monitor.add_argument("--once", action="store_true", help="Run one iteration and exit")
    monitor.add_argument("--task", help="Only emit for a specific task id (debug)")

    redirect = sub.add_parser(
        "redirect",
        help="Redirect agent: consume events and apply actions (wg log, follow-up tasks, pit-stops)",
    )
    redirect.add_argument("--interval", type=int, default=5, help="Poll interval seconds (default: 5)")
    redirect.add_argument("--once", action="store_true", help="Process available events once and exit")
    redirect.add_argument("--write-log", action="store_true", help="Write a drift summary to wg log")
    redirect.add_argument("--create-followups", action="store_true", help="Create follow-up tasks for drift findings")
    redirect.add_argument("--from-start", action="store_true", help="Ignore stored cursor and replay events from start")

    orch = sub.add_parser("orchestrate", help="Run monitor + redirect agents in parallel (two subprocesses)")
    orch.add_argument("--interval", type=int, default=30, help="Monitor poll interval seconds (default: 30)")
    orch.add_argument("--redirect-interval", type=int, default=5, help="Redirect poll interval seconds (default: 5)")
    orch.add_argument("--write-log", action="store_true", help="Write a drift summary to wg log (redirect agent)")
    orch.add_argument("--create-followups", action="store_true", help="Create follow-up tasks (redirect agent)")

    ensure = sub.add_parser(
        "ensure-contracts",
        help=f"Prepend default `{CONTRACT_FENCE_INFO}` block to open/in-progress tasks missing a contract",
    )
    ensure.add_argument("--apply", action="store_true", help="Apply edits to graph.jsonl (otherwise dry-run)")
    ensure.add_argument("--only-open", action="store_true", help="Only modify open tasks (skip in-progress)")

    contract = sub.add_parser("contract", help="View or edit a task's wg-contract block")
    contract_sub = contract.add_subparsers(dest="contract_cmd", required=True)

    c_show = contract_sub.add_parser("show", help="Show parsed contract for a task")
    c_show.add_argument("--task", help="Task id")
    c_show.add_argument("--json", action="store_true", help="Output as JSON")

    c_touch = contract_sub.add_parser("set-touch", help="Replace the contract touch globs for a task")
    c_touch.add_argument("--task", help="Task id")
    c_touch.add_argument("touch", nargs="+", help="Repo-root-relative glob(s), e.g. src/** **/*.md")

    return p.parse_args(argv)


def _choose_task_id(wg: Workgraph) -> str:
    in_progress = [t for t in wg.tasks.values() if t.get("status") == "in-progress"]
    if len(in_progress) == 1:
        return str(in_progress[0]["id"])
    if not in_progress:
        raise ValueError("No in-progress tasks found; pass --task <id>.")
    raise ValueError(f"Multiple in-progress tasks found ({len(in_progress)}); pass --task <id>.")


def _emit_text(report: dict[str, Any]) -> None:
    task_id = report.get("task_id")
    title = report.get("task_title")
    score = report.get("score")
    findings = report.get("findings", [])

    print(f"{task_id}: {title}")
    print(f"score: {score}")
    if not findings:
        print("findings: none")
        return

    print("findings:")
    for f in findings:
        kind = f.get("kind")
        sev = f.get("severity")
        summary = f.get("summary")
        print(f"- [{sev}] {kind}: {summary}")


def _emit_json(obj: Any) -> None:
    json.dump(obj, sys.stdout, indent=2, sort_keys=False)
    sys.stdout.write("\n")


def _maybe_write_log(wg: Workgraph, task_id: str, report: dict[str, Any]) -> None:
    findings = report.get("findings", [])
    score = report.get("score", "unknown")
    recs = report.get("recommendations", [])

    if not findings:
        msg = "Speedrift: OK (no findings)"
    else:
        kinds = ", ".join(sorted({str(f.get('kind')) for f in findings}))
        msg = f"Speedrift: {score} ({kinds})"
        if recs:
            next_action = str(recs[0].get("action") or "").strip()
            if next_action:
                msg += f" | next: {next_action}"

    wg.wg_log(task_id, msg)


def _maybe_create_followups(wg: Workgraph, report: dict[str, Any]) -> None:
    task_id = str(report["task_id"])
    task_title = str(report.get("task_title") or task_id)
    contract = report.get("contract") or {}
    mode = contract.get("mode", "core")
    if contract.get("auto_followups", True) is False:
        return

    if mode != "core":
        return

    findings = report.get("findings", [])
    if not findings:
        return

    # Create a small number of deterministic follow-ups.
    for f in findings:
        kind = str(f.get("kind") or "")
        if kind == "hardening_in_core":
            follow_id = f"drift-harden-{task_id}"
            title = f"harden: {task_title}"
            desc = (
                "Move guardrails/fallbacks out of core execution.\n\n"
                "Context:\n"
                f"- Origin task: {task_id}\n"
                f"- Finding: {f.get('summary')}\n\n"
                + format_default_contract_block(mode="harden", objective=title, touch=contract.get("touch") or [])
            )
            wg.ensure_task(
                task_id=follow_id,
                title=title,
                description=desc,
                blocked_by=[task_id],
                tags=["drift", "harden"],
            )
        elif kind == "scope_drift":
            follow_id = f"drift-scope-{task_id}"
            title = f"scope: {task_title}"
            desc = (
                "Triage out-of-scope file changes (update contract touch set or revert).\n\n"
                "Context:\n"
                f"- Origin task: {task_id}\n"
                f"- Finding: {f.get('summary')}\n\n"
                + format_default_contract_block(mode="explore", objective=title, touch=contract.get("touch") or [])
            )
            wg.ensure_task(
                task_id=follow_id,
                title=title,
                description=desc,
                blocked_by=[task_id],
                tags=["drift", "scope"],
            )

def _maybe_create_pit_stop(wg: Workgraph, report: dict[str, Any], *, streak: int) -> str | None:
    task_id = str(report["task_id"])
    task_title = str(report.get("task_title") or task_id)
    contract = report.get("contract") or {}
    mode = contract.get("mode", "core")
    if contract.get("auto_followups", True) is False:
        return None
    if mode != "core":
        return None

    pit_id = f"speedrift-pit-{task_id}"
    title = f"pit-stop: {task_title}"

    findings = report.get("findings", [])
    recs = report.get("recommendations", [])

    findings_text = ""
    if findings:
        findings_text = "\n".join([f"- {f.get('kind')}: {f.get('summary')}" for f in findings])

    recs_text = ""
    if recs:
        recs_text = "\n".join([f"- {r.get('action')}" for r in recs[:5]])

    desc = (
        "Persistent drift detected.\n\n"
        f"Origin: {task_id}\n"
        f"Streak: {streak}\n\n"
        "Findings:\n"
        f"{findings_text or '(none)'}\n\n"
        "Countersteer (recommended next actions):\n"
        f"{recs_text or '(none)'}\n\n"
        + format_default_contract_block(mode="explore", objective=title, touch=contract.get("touch") or [])
    )

    # Keep it non-blocking (task is created but depends on the origin task).
    wg.ensure_task(
        task_id=pit_id,
        title=title,
        description=desc,
        blocked_by=[task_id],
        tags=["speedrift", "pit-stop", "drift"],
    )
    return pit_id


def _report_for_task(wg: Workgraph, task_id: str) -> dict[str, Any]:
    task = wg.tasks.get(task_id)
    if not task:
        raise ValueError(f"Task not found: {task_id}")

    description = str(task.get("description") or "")
    contract_text = extract_contract(description)
    contract: TaskContract | None = None
    contract_raw: dict[str, Any] | None = None
    if contract_text is not None:
        try:
            contract_raw = parse_contract(contract_text)
            contract = TaskContract.from_raw(contract_raw, fallback_objective=str(task.get("title") or task_id))
        except Exception as e:
            contract_raw = {"parse_error": str(e)}

    git_root = get_git_root(wg.project_dir)
    changes = get_working_changes(git_root) if git_root else None

    return compute_drift(
        task_id=task_id,
        task_title=str(task.get("title") or ""),
        description=description,
        contract=contract,
        contract_raw=contract_raw,
        git_root=git_root,
        changes=changes,
    )


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)

    try:
        if args.cmd == "install":
            # Resolve or initialize workgraph directory.
            base = Path(args.dir).expanduser() if args.dir else Path.cwd()
            wg_dir = base if base.name == ".workgraph" else base / ".workgraph"
            if not (wg_dir / "graph.jsonl").exists():
                import subprocess

                subprocess.check_call(["wg", "init", "--dir", str(wg_dir)])

            # Create wrapper and guidance in the target project.
            speedrift_bin = (Path(__file__).resolve().parents[1] / "bin" / "speedrift").resolve()
            write_speedrift_wrapper(wg_dir, speedrift_bin=speedrift_bin)
            ensure_speedrift_gitignore(wg_dir)

            include_uxrift = False
            uxrift_bin: Path | None = None
            if args.uxrift_bin:
                uxrift_bin = Path(args.uxrift_bin).expanduser().resolve()
                if not uxrift_bin.exists():
                    raise ValueError(f"uxrift bin not found: {uxrift_bin}")
                include_uxrift = True
            elif args.with_uxrift:
                candidates: list[Path] = []

                env_bin = os.environ.get("UXRIFT_BIN")
                if env_bin:
                    candidates.append(Path(env_bin).expanduser())

                # Convenience for "side-by-side" checkouts (common in this workspace).
                repo_root = Path(__file__).resolve().parents[1]
                candidates.append(repo_root.parent / "uxrift" / "bin" / "uxrift")

                which = shutil.which("uxrift")
                if which:
                    candidates.append(Path(which))

                for c in candidates:
                    try:
                        resolved = c.resolve()
                    except Exception:
                        resolved = c
                    if resolved.exists() and os.access(resolved, os.X_OK):
                        uxrift_bin = resolved
                        include_uxrift = True
                        break

                if not include_uxrift:
                    print(
                        "note: uxrift not found (set UXRIFT_BIN or pass --uxrift-bin); skipping uxrift integration",
                        file=sys.stderr,
                    )

            if include_uxrift and uxrift_bin is not None:
                write_uxrift_wrapper(wg_dir, uxrift_bin=uxrift_bin)
                ensure_uxrift_gitignore(wg_dir)

            ensure_executor_guidance(wg_dir, include_uxrift=include_uxrift)

            if not args.no_ensure_contracts:
                rewrite_graph_with_contracts(wg_dir=wg_dir, statuses={"open", "in-progress"}, apply=True)

            msg = f"Installed Speedrift into {wg_dir}"
            if include_uxrift:
                msg += " (with uxrift)"
            print(msg)
            return ExitCode.ok

        wg_dir = find_workgraph_dir(Path(args.dir) if args.dir else None)
        wg = load_workgraph(wg_dir)

        if args.cmd == "ensure-contracts":
            statuses: set[str]
            if args.only_open:
                statuses = {"open"}
            else:
                statuses = {"open", "in-progress"}

            patch = rewrite_graph_with_contracts(
                wg_dir=wg_dir,
                statuses=statuses,
                apply=args.apply,
            )
            if args.apply:
                print(f"Updated tasks: {patch.updated_tasks}")
            else:
                print(f"Would update tasks: {patch.updated_tasks}")
            return ExitCode.ok

        if args.cmd == "contract":
            task_id = getattr(args, "task", None) or _choose_task_id(wg)
            task = wg.tasks.get(task_id)
            if not task:
                raise ValueError(f"Task not found: {task_id}")
            description = str(task.get("description") or "")
            contract_text = extract_contract(description)

            if args.contract_cmd == "show":
                if contract_text is None:
                    if args.json:
                        _emit_json({"task_id": task_id, "contract": None})
                    else:
                        print(f"{task_id}: (no contract)")
                    return ExitCode.ok
                try:
                    raw = parse_contract(contract_text)
                except Exception as e:
                    raw = {"parse_error": str(e)}
                if args.json:
                    _emit_json({"task_id": task_id, "contract": raw})
                else:
                    _emit_text({"task_id": task_id, "task_title": task.get("title"), "score": "n/a", "findings": []})
                    print(contract_text)
                return ExitCode.ok

            if args.contract_cmd == "set-touch":
                title = str(task.get("title") or task_id)
                raw: dict[str, Any]
                if contract_text is None:
                    raw = {
                        "schema": 1,
                        "mode": "core",
                        "objective": title,
                        "non_goals": DEFAULT_NON_GOALS,
                        "touch": [],
                        "acceptance": [],
                        "max_files": 25,
                        "max_loc": 800,
                        "pit_stop_after": 3,
                        "auto_followups": True,
                    }
                else:
                    raw = parse_contract(contract_text)

                raw["touch"] = list(args.touch)
                new_desc = replace_contract_block(description, raw)
                update_task_description(wg_dir=wg_dir, task_id=task_id, new_description=new_desc)
                print(f"Updated contract touch for {task_id}: {len(args.touch)} globs")
                return ExitCode.ok

            raise ValueError(f"Unknown contract subcommand: {args.contract_cmd}")

        if args.cmd == "monitor":
            import time

            while True:
                wg = load_workgraph(wg_dir)
                if args.task:
                    task_ids = [str(args.task)]
                else:
                    task_ids = [str(t["id"]) for t in wg.tasks.values() if t.get("status") == "in-progress"]

                for task_id in task_ids:
                    report = _report_for_task(wg, task_id)
                    append_event(wg_dir, {"kind": "drift_report", **report})

                if args.once:
                    return ExitCode.ok
                time.sleep(max(1, int(args.interval)))

        if args.cmd == "redirect":
            import time

            p = events_path(wg_dir)
            from_start = bool(args.from_start)

            while True:
                wg = load_workgraph(wg_dir)
                with locked_state(wg_dir) as state:
                    cursor = 0 if from_start else int(state.get("event_cursor") or 0)
                    events, new_cursor = read_events_since(p, cursor)

                    if not events:
                        # Still persist cursor if file got truncated.
                        state["event_cursor"] = cursor

                if not events:
                    if args.once:
                        return ExitCode.ok
                    time.sleep(max(1, int(args.interval)))
                    continue

                from_start = False

                with locked_state(wg_dir) as state:
                    for ev in events:
                        if not isinstance(ev, dict):
                            continue
                        if ev.get("kind") not in (None, "drift_report"):
                            continue
                        report = ev.get("report") if isinstance(ev.get("report"), dict) else ev
                        task_id = report.get("task_id")
                        if not task_id:
                            continue
                        task_id = str(task_id)

                        kinds = sorted({str(f.get("kind")) for f in report.get("findings", [])})
                        prev = (
                            ((state.get("tasks") or {}).get(task_id) or {}) if isinstance(state.get("tasks"), dict) else {}
                        )
                        prev_sig = (str(prev.get("score") or "green"), tuple(prev.get("kinds") or ()))
                        cur_sig = (str(report.get("score") or "green"), tuple(kinds))

                        contract = report.get("contract") or {}
                        pit_stop_after = int(contract.get("pit_stop_after") or 3)

                        upd = update_task_state(
                            state=state,
                            task_id=task_id,
                            score=str(report.get("score")),
                            kinds=kinds,
                            pit_stop_after=pit_stop_after,
                        )

                        sig_changed = prev_sig != cur_sig
                        if args.write_log and (sig_changed or upd.pit_stop_due):
                            _maybe_write_log(wg, task_id, report)

                        if args.create_followups and (sig_changed or upd.pit_stop_due):
                            _maybe_create_followups(wg, report)
                            if upd.pit_stop_due:
                                pit_id = _maybe_create_pit_stop(wg, report, streak=upd.streak)
                                if pit_id:
                                    mark_pit_stop_created(state=state, task_id=task_id)

                    state["event_cursor"] = int(new_cursor)

                if args.once:
                    return ExitCode.ok
                time.sleep(max(1, int(args.interval)))

        if args.cmd == "orchestrate":
            import subprocess

            repo_root = Path(__file__).resolve().parents[1]
            env = os.environ.copy()
            env["PYTHONPATH"] = str(repo_root) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")

            dir_arg = str(wg_dir)

            monitor_cmd = [
                sys.executable,
                "-m",
                "wg_drift",
                "--dir",
                dir_arg,
                "monitor",
                "--interval",
                str(int(args.interval)),
            ]
            redirect_cmd = [
                sys.executable,
                "-m",
                "wg_drift",
                "--dir",
                dir_arg,
                "redirect",
                "--interval",
                str(int(args.redirect_interval)),
            ]
            if args.write_log:
                redirect_cmd.append("--write-log")
            if args.create_followups:
                redirect_cmd.append("--create-followups")

            p_mon = subprocess.Popen(monitor_cmd, env=env)
            p_red = subprocess.Popen(redirect_cmd, env=env)

            try:
                while True:
                    mon_rc = p_mon.poll()
                    red_rc = p_red.poll()
                    if mon_rc is not None:
                        p_red.terminate()
                        return int(mon_rc)
                    if red_rc is not None:
                        p_mon.terminate()
                        return int(red_rc)

                    import time

                    time.sleep(0.5)
            except KeyboardInterrupt:
                p_mon.terminate()
                p_red.terminate()
                return ExitCode.error

        if args.cmd == "check":
            task_id = args.task or _choose_task_id(wg)
            report = _report_for_task(wg, task_id)

            with locked_state(wg_dir) as state:
                contract = report.get("contract") or {}
                pit_stop_after = int(contract.get("pit_stop_after") or 3)
                kinds = sorted({str(f.get("kind")) for f in report.get("findings", [])})
                upd = update_task_state(
                    state=state,
                    task_id=task_id,
                    score=str(report.get("score")),
                    kinds=kinds,
                    pit_stop_after=pit_stop_after,
                )

                if args.write_log:
                    _maybe_write_log(wg, task_id, report)
                if args.create_followups:
                    _maybe_create_followups(wg, report)
                    if upd.pit_stop_due:
                        pit_id = _maybe_create_pit_stop(wg, report, streak=upd.streak)
                        if pit_id:
                            mark_pit_stop_created(state=state, task_id=task_id)

            if args.json:
                _emit_json(report)
            else:
                _emit_text(report)
            return ExitCode.drift_found if report.get("findings") else ExitCode.ok

        if args.cmd == "scan":
            in_progress = [t for t in wg.tasks.values() if t.get("status") == "in-progress"]
            reports = []
            with locked_state(wg_dir) as state:
                for t in in_progress:
                    task_id = str(t["id"])
                    report = _report_for_task(wg, task_id)
                    reports.append(report)

                    contract = report.get("contract") or {}
                    pit_stop_after = int(contract.get("pit_stop_after") or 3)
                    kinds = sorted({str(f.get("kind")) for f in report.get("findings", [])})
                    upd = update_task_state(
                        state=state,
                        task_id=task_id,
                        score=str(report.get("score")),
                        kinds=kinds,
                        pit_stop_after=pit_stop_after,
                    )

                    if args.write_log:
                        _maybe_write_log(wg, task_id, report)
                    if args.create_followups:
                        _maybe_create_followups(wg, report)
                        if upd.pit_stop_due:
                            pit_id = _maybe_create_pit_stop(wg, report, streak=upd.streak)
                            if pit_id:
                                mark_pit_stop_created(state=state, task_id=task_id)

            if args.json:
                _emit_json({"reports": reports})
            else:
                for r in reports:
                    _emit_text(r)
                    print()
            any_findings = any(r.get("findings") for r in reports)
            return ExitCode.drift_found if any_findings else ExitCode.ok

        if args.cmd == "watch":
            while True:
                # Reload each tick so we see new claims/completions without restart.
                wg = load_workgraph(wg_dir)
                in_progress = [t for t in wg.tasks.values() if t.get("status") == "in-progress"]
                reports = []
                with locked_state(wg_dir) as state:
                    for t in in_progress:
                        task_id = str(t["id"])
                        report = _report_for_task(wg, task_id)
                        reports.append(report)

                        kinds = sorted({str(f.get("kind")) for f in report.get("findings", [])})
                        prev = (
                            ((state.get("tasks") or {}).get(task_id) or {}) if isinstance(state.get("tasks"), dict) else {}
                        )
                        prev_sig = (str(prev.get("score") or "green"), tuple(prev.get("kinds") or ()))
                        cur_sig = (str(report.get("score") or "green"), tuple(kinds))

                        contract = report.get("contract") or {}
                        pit_stop_after = int(contract.get("pit_stop_after") or 3)
                        upd = update_task_state(
                            state=state,
                            task_id=task_id,
                            score=str(report.get("score")),
                            kinds=kinds,
                            pit_stop_after=pit_stop_after,
                        )

                        sig_changed = prev_sig != cur_sig
                        if args.write_log and (sig_changed or upd.pit_stop_due):
                            _maybe_write_log(wg, task_id, report)

                        if args.create_followups and (sig_changed or upd.pit_stop_due):
                            _maybe_create_followups(wg, report)
                            if upd.pit_stop_due:
                                pit_id = _maybe_create_pit_stop(wg, report, streak=upd.streak)
                                if pit_id:
                                    mark_pit_stop_created(state=state, task_id=task_id)

                    if args.json:
                        _emit_json({"reports": reports})

                import time

                time.sleep(max(1, int(args.interval)))

        raise ValueError(f"Unknown command: {args.cmd}")
    except KeyboardInterrupt:
        return ExitCode.error
    except Exception as e:
        print(f"error: {e}", file=sys.stderr)
        return ExitCode.error
