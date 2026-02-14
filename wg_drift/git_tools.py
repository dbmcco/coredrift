from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path


def get_git_root(project_dir: Path) -> str | None:
    try:
        out = subprocess.check_output(
            ["git", "-C", str(project_dir), "rev-parse", "--show-toplevel"],
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
        return out or None
    except Exception:
        return None


@dataclass(frozen=True)
class WorkingChanges:
    changed_files: list[str]
    loc_changed: int
    added_lines: list[str]


def _git_lines(args: list[str], *, cwd: str) -> list[str]:
    try:
        out = subprocess.check_output(["git", "-C", cwd, *args], text=True, stderr=subprocess.DEVNULL)
    except Exception:
        return []
    return [l for l in out.splitlines() if l.strip()]


def get_working_changes(git_root: str) -> WorkingChanges:
    # Collect changed file paths (staged, unstaged, and untracked).
    unstaged = set(_git_lines(["diff", "--name-only"], cwd=git_root))
    staged = set(_git_lines(["diff", "--name-only", "--cached"], cwd=git_root))
    untracked = set(_git_lines(["ls-files", "--others", "--exclude-standard"], cwd=git_root))

    changed = sorted(unstaged | staged | untracked)

    # LOC churn (added + deleted) from staged + unstaged
    numstats = _git_lines(["diff", "--numstat"], cwd=git_root) + _git_lines(["diff", "--numstat", "--cached"], cwd=git_root)
    loc_changed = 0
    for line in numstats:
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        add_s, del_s = parts[0], parts[1]
        try:
            add_n = int(add_s) if add_s != "-" else 0
            del_n = int(del_s) if del_s != "-" else 0
            loc_changed += add_n + del_n
        except ValueError:
            continue

    # Added lines (best-effort). Filter out .workgraph noise to avoid false positives.
    added_lines: list[str] = []

    def collect_added(diff_text: str) -> None:
        cur_file: str | None = None
        include_file = True
        for line in diff_text.splitlines():
            if line.startswith("diff --git "):
                # Example: diff --git a/src/app.py b/src/app.py
                parts = line.split()
                if len(parts) >= 4 and parts[3].startswith("b/"):
                    cur_file = parts[3][2:]
                    include_file = not (cur_file.startswith(".workgraph/") or cur_file.startswith(".git/"))
                else:
                    cur_file = None
                    include_file = True
                continue

            if not include_file:
                continue

            if not line.startswith("+"):
                continue
            if line.startswith("+++"):
                continue
            added_lines.append(line[1:])

    try:
        collect_added(
            subprocess.check_output(["git", "-C", git_root, "diff", "--unified=0"], text=True, stderr=subprocess.DEVNULL)
        )
    except Exception:
        pass
    try:
        collect_added(
            subprocess.check_output(
                ["git", "-C", git_root, "diff", "--unified=0", "--cached"], text=True, stderr=subprocess.DEVNULL
            )
        )
    except Exception:
        pass

    return WorkingChanges(changed_files=changed, loc_changed=loc_changed, added_lines=added_lines)
