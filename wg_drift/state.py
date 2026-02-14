from __future__ import annotations

import json
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def state_path(wg_dir: Path) -> Path:
    return wg_dir / ".speedrift" / "state.json"

def lock_path(wg_dir: Path) -> Path:
    return wg_dir / ".speedrift" / "state.lock"


def load_state_unlocked(wg_dir: Path) -> dict[str, Any]:
    p = state_path(wg_dir)
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    return {"schema": 1, "tasks": {}, "event_cursor": 0}


def save_state_unlocked(wg_dir: Path, state: dict[str, Any]) -> None:
    p = state_path(wg_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=False) + "\n", encoding="utf-8")
    tmp.replace(p)

@contextmanager
def locked_state(wg_dir: Path) -> Iterator[dict[str, Any]]:
    """
    Simple cross-process lock to avoid state corruption when multiple speedrift
    processes run (monitor/redirect, manual check, etc.).
    """

    # fcntl is available on macOS/Linux. If it's missing, we fall back to no lock.
    try:
        import fcntl  # type: ignore
    except Exception:  # pragma: no cover
        state = load_state_unlocked(wg_dir)
        yield state
        save_state_unlocked(wg_dir, state)
        return

    lp = lock_path(wg_dir)
    lp.parent.mkdir(parents=True, exist_ok=True)
    with lp.open("w", encoding="utf-8") as lockf:
        fcntl.flock(lockf.fileno(), fcntl.LOCK_EX)
        state = load_state_unlocked(wg_dir)
        try:
            yield state
        finally:
            save_state_unlocked(wg_dir, state)
            fcntl.flock(lockf.fileno(), fcntl.LOCK_UN)


def load_state(wg_dir: Path) -> dict[str, Any]:
    # Read-only helper (no lock). Prefer locked_state() for read-modify-write.
    return load_state_unlocked(wg_dir)


def save_state(wg_dir: Path, state: dict[str, Any]) -> None:
    # Write-only helper (no lock). Prefer locked_state() for read-modify-write.
    save_state_unlocked(wg_dir, state)


@dataclass(frozen=True)
class TaskStateUpdate:
    task_id: str
    streak: int
    pit_stop_due: bool
    pit_stop_created: bool


def update_task_state(
    *,
    state: dict[str, Any],
    task_id: str,
    score: str,
    kinds: list[str],
    pit_stop_after: int,
) -> TaskStateUpdate:
    tasks = state.setdefault("tasks", {})
    prev = tasks.get(task_id) if isinstance(tasks, dict) else None
    if not isinstance(prev, dict):
        prev = {}

    prev_score = str(prev.get("score") or "green")
    prev_streak = int(prev.get("streak") or 0)
    pit_stop_created = bool(prev.get("pit_stop_created", False))

    drifting = score != "green" and pit_stop_after > 0
    prev_drifting = prev_score != "green" and pit_stop_after > 0

    if drifting and prev_drifting:
        streak = prev_streak + 1
    elif drifting:
        streak = 1
    else:
        streak = 0

    pit_stop_due = drifting and (streak >= pit_stop_after) and (not pit_stop_created)

    tasks[task_id] = {
        "score": score,
        "kinds": kinds,
        "streak": streak,
        "pit_stop_created": pit_stop_created,
        "updated_at": _now_iso(),
    }

    return TaskStateUpdate(
        task_id=task_id,
        streak=streak,
        pit_stop_due=pit_stop_due,
        pit_stop_created=pit_stop_created,
    )


def mark_pit_stop_created(*, state: dict[str, Any], task_id: str) -> None:
    tasks = state.setdefault("tasks", {})
    prev = tasks.get(task_id) if isinstance(tasks, dict) else None
    if not isinstance(prev, dict):
        prev = {}
    prev["pit_stop_created"] = True
    prev["updated_at"] = _now_iso()
    tasks[task_id] = prev
