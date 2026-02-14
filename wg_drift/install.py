from __future__ import annotations

import re
import stat
from dataclasses import dataclass
from pathlib import Path

SPEEDRIFT_MARKER = "## Speedrift Protocol"
UXRIFT_MARKER = "## uxrift Protocol"


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


def ensure_uxrift_gitignore(wg_dir: Path) -> bool:
    return _ensure_line_in_file(wg_dir / ".gitignore", ".uxrift/")


def write_tool_wrapper(wg_dir: Path, *, tool_name: str, tool_bin: Path) -> bool:
    """
    Writes .workgraph/<tool_name> wrapper pointing at a tool checkout.
    Returns True if file changed.
    """

    wrapper = wg_dir / tool_name
    content = (
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n\n"
        f'exec "{tool_bin}" "$@"\n'
    )

    existing = wrapper.read_text(encoding="utf-8") if wrapper.exists() else None
    changed = existing != content
    if changed:
        wrapper.write_text(content, encoding="utf-8")
    wrapper.chmod(wrapper.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return changed


def write_speedrift_wrapper(wg_dir: Path, *, speedrift_bin: Path) -> bool:
    """
    Writes .workgraph/speedrift wrapper pointing at the current speedrift checkout.
    Returns True if file changed.
    """

    return write_tool_wrapper(wg_dir, tool_name="speedrift", tool_bin=speedrift_bin)


def write_uxrift_wrapper(wg_dir: Path, *, uxrift_bin: Path) -> bool:
    """
    Writes .workgraph/uxrift wrapper pointing at a uxrift checkout.
    Returns True if file changed.
    """

    return write_tool_wrapper(wg_dir, tool_name="uxrift", tool_bin=uxrift_bin)


def write_rifts_wrapper(wg_dir: Path) -> bool:
    """
    Writes .workgraph/rifts wrapper that runs speedrift (always) and uxrift (best-effort).

    This is intentionally a simple bash script so it can live inside any workgraph repo
    without requiring extra installs.
    """

    wrapper = wg_dir / "rifts"
    content = (
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n\n"
        "usage() {\n"
        "  cat <<'EOF'\n"
        "workgraph rifts\n\n"
        "Usage:\n"
        "  ./.workgraph/rifts [--dir <path>] check --task <id> [--write-log] [--create-followups]\n\n"
        "Behavior:\n"
        "- Always runs speedrift for code drift.\n"
        "- Runs uxrift only if ./.workgraph/uxrift exists AND the task description contains a ```uxrift block.\n"
        "EOF\n"
        "}\n\n"
        "DIR_ARG=\"\"\n"
        "if [[ \"${1:-}\" == \"--dir\" ]]; then\n"
        "  DIR_ARG=\"${2:-}\"\n"
        "  shift 2\n"
        "fi\n\n"
        "CMD=\"${1:-}\"\n"
        "shift || true\n\n"
        "if [[ \"$CMD\" == \"\" || \"$CMD\" == \"-h\" || \"$CMD\" == \"--help\" ]]; then\n"
        "  usage\n"
        "  exit 0\n"
        "fi\n\n"
        "if [[ \"$CMD\" != \"check\" ]]; then\n"
        "  echo \"error: only 'check' is supported\" >&2\n"
        "  usage >&2\n"
        "  exit 2\n"
        "fi\n\n"
        "# Determine .workgraph dir\n"
        "if [[ -n \"$DIR_ARG\" ]]; then\n"
        "  BASE=\"$DIR_ARG\"\n"
        "else\n"
        "  WG_DIR=\"$(cd \"$(dirname \"${BASH_SOURCE[0]}\")\" && pwd)\"\n"
        "  BASE=\"$(dirname \"$WG_DIR\")\"\n"
        "fi\n\n"
        "WG_DIR=\"$BASE\"\n"
        "if [[ \"$(basename \"$WG_DIR\")\" != \".workgraph\" ]]; then\n"
        "  WG_DIR=\"$BASE/.workgraph\"\n"
        "fi\n\n"
        "SPEEDRIFT=\"$WG_DIR/speedrift\"\n"
        "UXRIFT=\"$WG_DIR/uxrift\"\n\n"
        "TASK_ID=\"\"\n"
        "WRITE_LOG=0\n"
        "CREATE_FOLLOWUPS=0\n"
        "for ((i=1; i<=$#; i++)); do\n"
        "  arg=\"${!i}\"\n"
        "  if [[ \"$arg\" == \"--task\" ]]; then\n"
        "    j=$((i+1))\n"
        "    TASK_ID=\"${!j:-}\"\n"
        "  elif [[ \"$arg\" == \"--write-log\" ]]; then\n"
        "    WRITE_LOG=1\n"
        "  elif [[ \"$arg\" == \"--create-followups\" ]]; then\n"
        "    CREATE_FOLLOWUPS=1\n"
        "  elif [[ \"$arg\" == \"--json\" ]]; then\n"
        "    echo \"error: rifts check does not support --json; run ./.workgraph/speedrift check --json instead\" >&2\n"
        "    exit 2\n"
        "  fi\n"
        "done\n\n"
        "if [[ -z \"$TASK_ID\" ]]; then\n"
        "  echo \"error: --task is required\" >&2\n"
        "  usage >&2\n"
        "  exit 2\n"
        "fi\n\n"
        "set +e\n"
        "\"$SPEEDRIFT\" --dir \"$BASE\" check \"$@\"\n"
        "SPEED_RC=$?\n"
        "set -e\n\n"
        "if [[ \"$SPEED_RC\" -ne 0 && \"$SPEED_RC\" -ne 3 ]]; then\n"
        "  exit \"$SPEED_RC\"\n"
        "fi\n\n"
        "# uxrift is best-effort: skip if not installed or if task doesn't declare a uxrift block.\n"
        "UX_RC=0\n"
        "if [[ -x \"$UXRIFT\" ]]; then\n"
        "  HAS_UXRIFT_SPEC=0\n"
        "  if wg --dir \"$WG_DIR\" show \"$TASK_ID\" --json 2>/dev/null | python3 -c 'import json,sys; t=json.load(sys.stdin); d=t.get(\"description\") or \"\"; sys.exit(0 if \"```uxrift\" in d else 1)'; then\n"
        "    HAS_UXRIFT_SPEC=1\n"
        "  fi\n\n"
        "  if [[ \"$HAS_UXRIFT_SPEC\" -eq 1 ]]; then\n"
        "    UX_ARGS=(\"wg\" \"--dir\" \"$BASE\" \"check\" \"--task\" \"$TASK_ID\")\n"
        "    if [[ \"$WRITE_LOG\" -eq 1 ]]; then UX_ARGS+=(\"--write-log\"); fi\n"
        "    if [[ \"$CREATE_FOLLOWUPS\" -eq 1 ]]; then UX_ARGS+=(\"--create-followups\"); fi\n"
        "    set +e\n"
        "    \"$UXRIFT\" \"${UX_ARGS[@]}\"\n"
        "    UX_RC=$?\n"
        "    set -e\n"
        "    if [[ \"$UX_RC\" -ne 0 && \"$UX_RC\" -ne 3 ]]; then\n"
        "      echo \"note: uxrift failed (exit $UX_RC); continuing\" >&2\n"
        "      UX_RC=0\n"
        "    fi\n"
        "  fi\n"
        "fi\n\n"
        "if [[ \"$SPEED_RC\" -eq 3 || \"$UX_RC\" -eq 3 ]]; then\n"
        "  exit 3\n"
        "fi\n"
        "exit 0\n"
    )

    existing = wrapper.read_text(encoding="utf-8") if wrapper.exists() else None
    changed = existing != content
    if changed:
        wrapper.write_text(content, encoding="utf-8")
    wrapper.chmod(wrapper.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return changed


def _default_claude_executor_text(*, project_dir: Path, include_uxrift: bool) -> str:
    # Keep this lightweight and generic: the "contract" is the task description.
    uxrift = ""
    if include_uxrift:
        uxrift = (
            "\n"
            f"{UXRIFT_MARKER}\n"
            "- If this task includes a `uxrift` block (in the description), run:\n"
            f"  ./.workgraph/uxrift wg check --task {{{{task_id}}}} --write-log --create-followups\n"
            "- Or run the unified check (runs uxrift when a spec is present):\n"
            f"  ./.workgraph/rifts check --task {{{{task_id}}}} --write-log --create-followups\n"
            "- If it fails due to missing URL, set `url = \"...\"` in the `uxrift` block or pass --url.\n"
        )

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
  ./.workgraph/rifts check --task {{{{task_id}}}} --write-log --create-followups
- If you need to change scope, update touch globs:
  ./.workgraph/speedrift contract set-touch --task {{{{task_id}}}} <globs...>
- If Speedrift flags `hardening_in_core`, do NOT add guardrails here; create/complete the `harden:` follow-up task.
{uxrift}

## Workgraph Rules
- Stay focused on this task.
- Log progress: wg log {{{{task_id}}}} \"message\"
- When complete: wg done {{{{task_id}}}}
- If blocked: wg fail {{{{task_id}}}} --reason \"description\"
\"\"\"
"""


# Match TOML multiline string prompt templates:
# template = """
# ...
# """
_TEMPLATE_START_RE = re.compile(r'(?P<prefix>\btemplate\s*=\s*"""\r?\n)', re.MULTILINE)


def _inject_speedrift_into_template(body: str) -> str | None:
    """
    Returns modified file text, or None if no changes needed/possible.
    """

    if SPEEDRIFT_MARKER in body:
        # Upgrade existing protocol blocks in-place if they reference speedrift directly.
        old = "  ./.workgraph/speedrift check --task {{task_id}} --write-log --create-followups"
        new = "  ./.workgraph/rifts check --task {{task_id}} --write-log --create-followups"
        upgraded = body.replace(old, new)
        if upgraded != body:
            return upgraded
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
        "  ./.workgraph/rifts check --task {{task_id}} --write-log --create-followups\n"
        "- If you need to change scope, update touch globs:\n"
        "  ./.workgraph/speedrift contract set-touch --task {{task_id}} <globs...>\n"
        "- If Speedrift flags `hardening_in_core`, do NOT add guardrails here; create/complete the `harden:` follow-up task.\n"
    )

    # Insert right before the closing triple quotes.
    new_body = body[:end].rstrip("\n") + "\n" + insert + "\n" + body[end:]
    return new_body


def _inject_uxrift_into_template(body: str) -> str | None:
    """
    Returns modified file text, or None if no changes needed/possible.
    """

    if UXRIFT_MARKER in body:
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
        f"{UXRIFT_MARKER}\n"
        "- If this task includes a `uxrift` block (in the description), run:\n"
        "  ./.workgraph/uxrift wg check --task {{task_id}} --write-log --create-followups\n"
        "- Or run the unified check (runs uxrift when a spec is present):\n"
        "  ./.workgraph/rifts check --task {{task_id}} --write-log --create-followups\n"
        "- If it fails due to missing URL, set `url = \"...\"` in the `uxrift` block or pass --url.\n"
        "- Artifacts live under `.workgraph/.uxrift/`.\n"
    )

    # Insert right before the closing triple quotes.
    new_body = body[:end].rstrip("\n") + "\n" + insert + "\n" + body[end:]
    return new_body


def ensure_executor_guidance(wg_dir: Path, *, include_uxrift: bool = False) -> tuple[bool, list[str]]:
    """
    Ensures .workgraph/executors exists, has a claude executor, and each executor template
    includes Speedrift guidance. Returns (created_claude_executor, patched_files).
    """

    executors_dir = wg_dir / "executors"
    executors_dir.mkdir(parents=True, exist_ok=True)

    created = False
    claude_path = executors_dir / "claude.toml"
    if not claude_path.exists():
        claude_path.write_text(
            _default_claude_executor_text(project_dir=wg_dir.parent, include_uxrift=include_uxrift),
            encoding="utf-8",
        )
        created = True

    patched: list[str] = []
    for p in sorted(executors_dir.glob("*.toml")):
        text = p.read_text(encoding="utf-8")
        cur = text
        changed = False

        new_text = _inject_speedrift_into_template(cur)
        if new_text is not None:
            cur = new_text
            changed = True

        if include_uxrift:
            new_text = _inject_uxrift_into_template(cur)
            if new_text is not None:
                cur = new_text
                changed = True

        if not changed:
            continue

        p.write_text(cur, encoding="utf-8")
        patched.append(str(p))

    return (created, patched)
