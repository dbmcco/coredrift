from __future__ import annotations

import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path

SPEEDRIFT_MARKER = "## Speedrift Protocol"


@dataclass(frozen=True)
class InstallResult:
    wrote_wrapper: bool
    updated_gitignore: bool
    created_executor: bool
    patched_executors: list[str]
    ensured_contracts: bool


def _ensure_line_in_file(path: Path, line: str) -> bool:
    """
    Ensures `line` exists as a standalone line in `path`. Returns True if file changed.
    """

    existing = ""
    if path.exists():
        existing = path.read_text(encoding="utf-8")
    lines = existing.splitlines()
    if any(l.strip() == line for l in lines):
        return False
    new = existing.rstrip("\n")
    if new:
        new += "\n"
    new += line + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(new, encoding="utf-8")
    return True


def ensure_speedrift_gitignore(wg_dir: Path) -> bool:
    return _ensure_line_in_file(wg_dir / ".gitignore", ".speedrift/")


def write_speedrift_wrapper(wg_dir: Path, *, speedrift_bin: Path) -> bool:
    """
    Writes .workgraph/speedrift wrapper pointing at the current speedrift checkout.
    Returns True if file changed.
    """

    wrapper = wg_dir / "speedrift"
    content = (
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n\n"
        f'exec "{speedrift_bin}" "$@"\n'
    )

    existing = wrapper.read_text(encoding="utf-8") if wrapper.exists() else None
    changed = existing != content
    if changed:
        wrapper.write_text(content, encoding="utf-8")
    wrapper.chmod(wrapper.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return changed


def _default_claude_executor_text(*, project_dir: Path) -> str:
    # Keep this lightweight and generic: the "contract" is the task description.
    return f"""[executor]
type = "claude"
command = "claude"
args = ["--print", "--dangerously-skip-permissions", "--no-session-persistence"]

[executor.prompt_template]
template = \"\"\"
You are working in: {project_dir}

Task: {{{{task_id}}}} - {{{{task_title}}}}

Description:
{{{{task_description}}}}

Context from dependencies:
{{{{task_context}}}}

{SPEEDRIFT_MARKER}
- Treat the `wg-contract` block (in the task description) as binding.
- At start and just before completion, run:
  ./.workgraph/speedrift check --task {{{{task_id}}}} --write-log --create-followups
- If you need to change scope, update touch globs:
  ./.workgraph/speedrift contract set-touch --task {{{{task_id}}}} <globs...>
- If Speedrift flags `hardening_in_core`, do NOT add guardrails here; create/complete the `harden:` follow-up task.

## Workgraph Rules
- Stay focused on this task.
- Log progress: wg log {{{{task_id}}}} \"message\"
- When complete: wg done {{{{task_id}}}}
- If blocked: wg fail {{{{task_id}}}} --reason \"description\"
\"\"\"
"""


_TEMPLATE_START_RE = re.compile(r"(?P<prefix>\\btemplate\\s*=\\s*\"\"\"\\n)", re.MULTILINE)


def _inject_speedrift_into_template(body: str) -> str | None:
    """
    Returns modified file text, or None if no changes needed/possible.
    """

    if SPEEDRIFT_MARKER in body:
        return None

    m = _TEMPLATE_START_RE.search(body)
    if not m:
        return None

    start = m.end("prefix")
    end = body.find('\"\"\"', start)
    if end == -1:
        return None

    insert = (
        "\n"
        f"{SPEEDRIFT_MARKER}\n"
        "- Treat the `wg-contract` block (in the task description) as binding.\n"
        "- At start and just before completion, run:\n"
        "  ./.workgraph/speedrift check --task {{task_id}} --write-log --create-followups\n"
        "- If you need to change scope, update touch globs:\n"
        "  ./.workgraph/speedrift contract set-touch --task {{task_id}} <globs...>\n"
        "- If Speedrift flags `hardening_in_core`, do NOT add guardrails here; create/complete the `harden:` follow-up task.\n"
    )

    # Insert right before the closing triple quotes.
    new_body = body[:end].rstrip("\n") + "\n" + insert + "\n" + body[end:]
    return new_body


def ensure_executor_guidance(wg_dir: Path) -> tuple[bool, list[str]]:
    """
    Ensures .workgraph/executors exists, has a claude executor, and each executor template
    includes Speedrift guidance. Returns (created_claude_executor, patched_files).
    """

    executors_dir = wg_dir / "executors"
    executors_dir.mkdir(parents=True, exist_ok=True)

    created = False
    claude_path = executors_dir / "claude.toml"
    if not claude_path.exists():
        claude_path.write_text(_default_claude_executor_text(project_dir=wg_dir.parent), encoding="utf-8")
        created = True

    patched: list[str] = []
    for p in sorted(executors_dir.glob("*.toml")):
        text = p.read_text(encoding="utf-8")
        new_text = _inject_speedrift_into_template(text)
        if new_text is None:
            continue
        p.write_text(new_text, encoding="utf-8")
        patched.append(str(p))

    return (created, patched)

