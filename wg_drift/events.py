from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def events_path(wg_dir: Path) -> Path:
    return wg_dir / ".coredrift" / "events.jsonl"


def append_event(wg_dir: Path, event: dict[str, Any]) -> None:
    p = events_path(wg_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    event = dict(event)
    event.setdefault("schema", 1)
    event.setdefault("timestamp", _now_iso())
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(event, separators=(",", ":")) + "\n")


def read_events_since(path: Path, offset: int) -> tuple[list[dict[str, Any]], int]:
    """
    Returns (events, new_offset). Best-effort parse; malformed lines are skipped.
    """

    try:
        size = path.stat().st_size
    except FileNotFoundError:
        return ([], offset)

    if offset > size:
        offset = 0

    events: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        f.seek(offset)
        while True:
            line = f.readline()
            if not line:
                break
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                if isinstance(obj, dict):
                    events.append(obj)
            except Exception:
                continue
        new_offset = f.tell()

    return (events, new_offset)

