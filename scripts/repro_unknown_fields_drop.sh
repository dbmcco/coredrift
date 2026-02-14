#!/usr/bin/env bash
set -euo pipefail

TMPDIR="$(mktemp -d)"
echo "tmp: $TMPDIR"
cd "$TMPDIR"

wg init >/dev/null
wg add "Repro" --id repro >/dev/null

echo "Before patch:"
cat .workgraph/graph.jsonl

python3 - <<'PY'
import json
from pathlib import Path

p = Path(".workgraph/graph.jsonl")
lines = p.read_text().splitlines()
obj = json.loads(lines[0])
obj["custom_field"] = {"hello": "world"}
lines[0] = json.dumps(obj, separators=(",", ":"))
p.write_text("\n".join(lines) + "\n")
PY

echo
echo "After patch:"
cat .workgraph/graph.jsonl

wg claim repro --actor tester >/dev/null
wg log repro "trigger rewrite" >/dev/null

echo
echo "After wg claim/log (custom_field will be gone):"
cat .workgraph/graph.jsonl

