from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from wg_drift.contracts import TaskContract, extract_contract
from wg_drift.git_tools import WorkingChanges
from wg_drift.globmatch import match_any


@dataclass(frozen=True)
class Finding:
    kind: str
    severity: str
    summary: str
    details: dict[str, Any] | None = None


def _hardening_signals(added_lines: list[str]) -> list[str]:
    signals: list[str] = []
    needles = [
        "fallback",
        "retry",
        "backoff",
        "timeout",
        "graceful",
        "guardrail",
        "defensive",
        "best effort",
        "silently",
        "swallow",
    ]

    for line in added_lines:
        lower = line.lower()
        if "except exception" in lower or lower.strip() == "except:":
            signals.append("broad exception handling")
        if "catch (" in lower:
            signals.append("catch added")
        for n in needles:
            if n in lower:
                signals.append(n)
    # de-dup stable order
    seen: set[str] = set()
    out: list[str] = []
    for s in signals:
        if s in seen:
            continue
        seen.add(s)
        out.append(s)
    return out


def compute_drift(
    *,
    task_id: str,
    task_title: str,
    description: str,
    contract: TaskContract | None,
    contract_raw: dict[str, Any] | None,
    git_root: str | None,
    changes: WorkingChanges | None,
) -> dict[str, Any]:
    findings: list[Finding] = []
    drift_files: list[str] = []
    if changes:
        drift_files = [p for p in changes.changed_files if not (p.startswith(".workgraph/") or p.startswith(".git/"))]

    telemetry: dict[str, Any] = {
        "files_changed": len(drift_files),
        "loc_changed": int(changes.loc_changed) if changes else 0,
    }

    if contract is None:
        contract_present = extract_contract(description) is not None
        if not contract_present:
            findings.append(
                Finding(
                    kind="missing_contract",
                    severity="warn",
                    summary="No wg-contract block found in task description",
                )
            )
        else:
            findings.append(
                Finding(
                    kind="invalid_contract",
                    severity="warn",
                    summary="wg-contract block present but could not be parsed",
                    details=contract_raw or None,
                )
            )
    else:
        if contract.mode == "core":
            # Scope drift
            if changes and contract.touch:
                out_of_scope = [p for p in drift_files if not match_any(p, contract.touch)]
                if out_of_scope:
                    telemetry["out_of_scope_files"] = len(out_of_scope)
                    findings.append(
                        Finding(
                            kind="scope_drift",
                            severity="warn",
                            summary=f"{len(out_of_scope)} files outside touch globs",
                            details={"out_of_scope": out_of_scope[:50]},
                        )
                    )

            # Dependency drift
            if changes:
                dep_files = [p for p in drift_files if p in _DEPENDENCY_FILES]
                if dep_files:
                    telemetry["dependency_files"] = dep_files
                    findings.append(
                        Finding(
                            kind="dependency_drift",
                            severity="warn",
                            summary=f"Dependency/lock files changed: {', '.join(dep_files)}",
                            details={"dep_files": dep_files},
                        )
                    )

            # Churn drift
            if changes and contract.max_files is not None and len(drift_files) > contract.max_files:
                findings.append(
                    Finding(
                        kind="churn_files",
                        severity="warn",
                        summary=f"High file churn: {len(drift_files)} files (max_files={contract.max_files})",
                        details={"changed_files": drift_files[:100]},
                    )
                )
            if changes and contract.max_loc is not None and changes.loc_changed > contract.max_loc:
                findings.append(
                    Finding(
                        kind="churn_loc",
                        severity="warn",
                        summary=f"High LOC churn: {changes.loc_changed} lines (max_loc={contract.max_loc})",
                        details={"loc_changed": changes.loc_changed},
                    )
                )

            # Hardening in core
            if changes:
                signals = _hardening_signals(changes.added_lines)
                if signals:
                    telemetry["hardening_signals"] = signals
                    findings.append(
                        Finding(
                            kind="hardening_in_core",
                            severity="warn",
                            summary=f"Possible hardening/fallback additions in core: {', '.join(signals[:6])}",
                            details={"signals": signals},
                        )
                    )

    score = "green"
    if any(f.severity == "warn" for f in findings):
        score = "yellow"
    if any(f.severity == "error" for f in findings):
        score = "red"

    recommendations: list[dict[str, Any]] = []
    for f in findings:
        kind = f.kind
        if kind == "missing_contract":
            recommendations.append(
                {
                    "priority": "high",
                    "action": "Add a wg-contract block to the task description",
                    "rationale": "Without an explicit contract, agents will improvise and scope will drift.",
                    "commands": ["coredrift ensure-contracts --apply"],
                }
            )
        elif kind == "invalid_contract":
            recommendations.append(
                {
                    "priority": "high",
                    "action": "Fix the wg-contract block TOML so it parses",
                    "rationale": "Coredrift can only enforce/advise against drift when it can read the contract.",
                }
            )
        elif kind == "scope_drift":
            recommendations.append(
                {
                    "priority": "high",
                    "action": "Revert out-of-scope file changes or expand touch globs",
                    "rationale": "Out-of-scope edits are the fastest path to unintended refactors and long-term performance regressions.",
                    "commands": ["coredrift contract set-touch --task <id> <glob...>"],
                }
            )
        elif kind == "hardening_in_core":
            recommendations.append(
                {
                    "priority": "high",
                    "action": "Move guardrails/fallbacks into a harden follow-up task",
                    "rationale": "Core tasks should ship the planned behavior; hardening is a separate mode to avoid accidental scope inflation.",
                }
            )
        elif kind == "dependency_drift":
            recommendations.append(
                {
                    "priority": "medium",
                    "action": "Confirm the dependency change is intentional; otherwise revert",
                    "rationale": "New dependencies and lockfile churn are sticky and often not required for the core objective.",
                }
            )
        elif kind.startswith("churn_"):
            recommendations.append(
                {
                    "priority": "medium",
                    "action": "Split the task or adjust max_files/max_loc budgets in the contract",
                    "rationale": "High churn correlates with spec drift and hidden refactors; smaller tasks keep the graph in sync.",
                }
            )

    # De-dupe by action while preserving order.
    seen_actions: set[str] = set()
    recommendations = [r for r in recommendations if not (r["action"] in seen_actions or seen_actions.add(r["action"]))]

    return {
        "task_id": task_id,
        "task_title": task_title,
        "git_root": git_root,
        "score": score,
        "contract": contract_raw or (asdict(contract) if contract else None),
        "telemetry": telemetry,
        "findings": [asdict(f) for f in findings],
        "recommendations": recommendations,
    }


_DEPENDENCY_FILES = {
    "package.json",
    "package-lock.json",
    "pnpm-lock.yaml",
    "yarn.lock",
    "bun.lockb",
    "Cargo.toml",
    "Cargo.lock",
    "go.mod",
    "go.sum",
    "requirements.txt",
    "requirements-dev.txt",
    "pyproject.toml",
    "Pipfile",
    "Pipfile.lock",
    "poetry.lock",
    "Gemfile",
    "Gemfile.lock",
}
