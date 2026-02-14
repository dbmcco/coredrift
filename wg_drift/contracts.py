from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from typing import Any

CONTRACT_FENCE_INFO = "wg-contract"
DEFAULT_NON_GOALS = ["No fallbacks/retries/guardrails unless acceptance requires it"]

_FENCE_RE = re.compile(
    r"```(?P<info>wg-contract)\s*\n(?P<body>.*?)\n```",
    re.DOTALL,
)


def extract_contract(description: str) -> str | None:
    m = _FENCE_RE.search(description or "")
    if not m:
        return None
    return m.group("body").strip()


def parse_contract(contract_text: str) -> dict[str, Any]:
    data = tomllib.loads(contract_text)
    if not isinstance(data, dict):
        raise ValueError("Contract must parse to a TOML table/object.")
    return data


def render_contract_toml(raw: dict[str, Any]) -> str:
    """
    Minimal TOML writer for our contract schema (kept intentionally small).

    This avoids external deps (tomlkit), and is deterministic for diffs.
    """

    def toml_string(s: str) -> str:
        # Keep it simple: strip quotes/newlines (contracts are meant to be one-liners).
        s2 = str(s).replace('"', "").replace("\n", " ").strip()
        return f'"{s2}"'

    def toml_list_str(xs: list[Any]) -> str:
        out = ["["]
        for x in xs:
            out.append(f"  {toml_string(str(x))},")
        out.append("]")
        return "\n".join(out)

    lines: list[str] = []

    # Stable ordering
    schema = int(raw.get("schema", 1))
    lines.append(f"schema = {schema}")

    if "mode" in raw:
        lines.append(f"mode = {toml_string(str(raw['mode']))}")
    if "objective" in raw:
        lines.append(f"objective = {toml_string(str(raw['objective']))}")

    non_goals = raw.get("non_goals")
    if non_goals is not None:
        lines.append(f"non_goals = {toml_list_str(list(non_goals))}")

    touch = raw.get("touch")
    if touch is not None:
        lines.append(f"touch = {toml_list_str(list(touch))}")

    acceptance = raw.get("acceptance")
    if acceptance is not None:
        lines.append(f"acceptance = {toml_list_str(list(acceptance))}")

    for k in ["max_files", "max_loc", "pit_stop_after"]:
        if k in raw and raw[k] is not None:
            try:
                lines.append(f"{k} = {int(raw[k])}")
            except Exception:
                pass

    if "auto_followups" in raw:
        lines.append(f"auto_followups = {'true' if bool(raw['auto_followups']) else 'false'}")

    return "\n".join(lines).rstrip() + "\n"


def render_contract_block(raw: dict[str, Any]) -> str:
    return f"```{CONTRACT_FENCE_INFO}\n{render_contract_toml(raw)}```\n"


def replace_contract_block(description: str, raw: dict[str, Any]) -> str:
    """
    Replace the first wg-contract fenced block if present; otherwise prepend.
    """

    new_block = render_contract_block(raw)
    if _FENCE_RE.search(description or ""):
        return _FENCE_RE.sub(new_block.rstrip("\n"), description or "", count=1).lstrip("\n")
    if (description or "").strip():
        return new_block + "\n" + (description or "")
    return new_block


def format_default_contract_block(
    *,
    mode: str = "core",
    objective: str = "",
    touch: list[str] | None = None,
) -> str:
    touch = touch or []
    return render_contract_block(
        {
            "schema": 1,
            "mode": mode,
            "objective": objective,
            "non_goals": DEFAULT_NON_GOALS,
            "touch": touch,
            "acceptance": [],
            "max_files": 25,
            "max_loc": 800,
            "pit_stop_after": 3,
            "auto_followups": True,
        }
    )


@dataclass(frozen=True)
class TaskContract:
    schema: int
    mode: str
    objective: str
    non_goals: list[str]
    touch: list[str]
    acceptance: list[str]
    max_files: int | None
    max_loc: int | None
    pit_stop_after: int | None
    auto_followups: bool

    @staticmethod
    def from_raw(raw: dict[str, Any], *, fallback_objective: str) -> "TaskContract":
        schema = int(raw.get("schema", 1))
        mode = str(raw.get("mode", "core"))
        objective = str(raw.get("objective") or fallback_objective)
        non_goals = [str(x) for x in (raw.get("non_goals") or [])]
        touch = [str(x) for x in (raw.get("touch") or [])]
        acceptance = [str(x) for x in (raw.get("acceptance") or [])]
        max_files = raw.get("max_files")
        max_loc = raw.get("max_loc")
        pit_stop_after = raw.get("pit_stop_after")
        auto_followups = bool(raw.get("auto_followups", True))

        return TaskContract(
            schema=schema,
            mode=mode,
            objective=objective,
            non_goals=non_goals,
            touch=touch,
            acceptance=acceptance,
            max_files=int(max_files) if max_files is not None else None,
            max_loc=int(max_loc) if max_loc is not None else None,
            pit_stop_after=int(pit_stop_after) if pit_stop_after is not None else None,
            auto_followups=auto_followups,
        )
