#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if ! command -v wg >/dev/null 2>&1; then
  echo "error: wg not found on PATH" >&2
  exit 1
fi

if ! command -v git >/dev/null 2>&1; then
  echo "error: git not found on PATH" >&2
  exit 1
fi

TMPDIR="$(mktemp -d)"
cleanup() { rm -rf "$TMPDIR"; }
trap cleanup EXIT

cd "$TMPDIR"
git init -q

mkdir -p src
cat > src/app.py <<'PY'
def main():
    print("hi")
PY

git add src/app.py
git commit -qm "init"

wg init >/dev/null

echo "0) install sets up wrapper + executor guidance"
"$ROOT/bin/speedrift" --dir "$TMPDIR" install >/dev/null
test -x "$TMPDIR/.workgraph/speedrift"
rg -n "## Speedrift Protocol" "$TMPDIR/.workgraph/executors/claude.toml" >/dev/null
rg -n "^\\.speedrift/$" "$TMPDIR/.workgraph/.gitignore" >/dev/null
echo "ok"

DESC_FILE="$(mktemp)"
cat > "$DESC_FILE" <<'MD'
```wg-contract
schema = 1
mode = "core"
objective = "core task"
non_goals = ["No fallbacks"]
touch = ["src/**"]
acceptance = []
max_files = 10
max_loc = 200
auto_followups = true
```

Do the thing.
MD

wg add "Core task" --id core-task -d "$(cat "$DESC_FILE")" >/dev/null
wg claim core-task --actor tester >/dev/null

# Introduce drift:
# - out-of-scope file
# - "fallback" signal in core code diff
echo "readme" > README.md
printf '\n# fallback path\n' >> src/app.py

echo "1) speedrift check reports findings"
set +e
REPORT="$(./.workgraph/speedrift --dir "$TMPDIR" check --json)"
CODE="$?"
set -e
if [[ "$CODE" -ne 0 && "$CODE" -ne 3 ]]; then
  echo "error: speedrift check failed with exit code $CODE" >&2
  exit "$CODE"
fi
python3 -c 'import json,sys; r=json.loads(sys.stdin.read()); kinds={f["kind"] for f in r.get("findings", [])}; assert "scope_drift" in kinds, kinds; assert "hardening_in_core" in kinds, kinds; print("ok")' <<<"$REPORT"

echo "2) speedrift can write wg log and create follow-up tasks"
set +e
./.workgraph/speedrift --dir "$TMPDIR" check --write-log --create-followups >/dev/null
CODE="$?"
set -e
if [[ "$CODE" -ne 0 && "$CODE" -ne 3 ]]; then
  echo "error: speedrift check (write-log/followups) failed with exit code $CODE" >&2
  exit "$CODE"
fi

wg show --dir "$TMPDIR/.workgraph" core-task --json | python3 -c 'import json,sys; t=json.load(sys.stdin); msgs=[e.get("message","") for e in t.get("log",[])]; assert any(m.startswith("Speedrift:") for m in msgs), msgs; print("ok")'

wg show --dir "$TMPDIR/.workgraph" drift-harden-core-task --json >/dev/null
wg show --dir "$TMPDIR/.workgraph" drift-scope-core-task --json >/dev/null

echo "3) pit-stop escalation after consecutive drift"
set +e
./.workgraph/speedrift --dir "$TMPDIR" check --create-followups >/dev/null
CODE="$?"
set -e
if [[ "$CODE" -ne 0 && "$CODE" -ne 3 ]]; then
  echo "error: speedrift check (pit-stop) failed with exit code $CODE" >&2
  exit "$CODE"
fi
wg show --dir "$TMPDIR/.workgraph" speedrift-pit-core-task --json >/dev/null
echo "ok"

echo "4) monitor+redirect pipeline emits events and applies actions"
wg add "Core task 2" --id core-task-2 -d "$(cat "$DESC_FILE")" >/dev/null
wg claim core-task-2 --actor tester >/dev/null
echo "readme2" > README2.md
printf '\n# fallback added\n' >> src/app.py

./.workgraph/speedrift --dir "$TMPDIR" monitor --once >/dev/null
test -s "$TMPDIR/.workgraph/.speedrift/events.jsonl"

set +e
./.workgraph/speedrift --dir "$TMPDIR" redirect --once --write-log --create-followups --from-start >/dev/null
CODE="$?"
set -e
if [[ "$CODE" -ne 0 && "$CODE" -ne 3 ]]; then
  echo "error: speedrift redirect failed with exit code $CODE" >&2
  exit "$CODE"
fi
wg show --dir "$TMPDIR/.workgraph" drift-harden-core-task-2 --json >/dev/null
wg show --dir "$TMPDIR/.workgraph" drift-scope-core-task-2 --json >/dev/null
echo "ok"

echo "5) contract set-touch rewrites description"
./.workgraph/speedrift --dir "$TMPDIR" contract set-touch --task core-task "src/**" "tests/**" >/dev/null
wg show --dir "$TMPDIR/.workgraph" core-task --json | python3 -c 'import json,sys; t=json.load(sys.stdin); d=t.get("description") or ""; assert "tests/**" in d, d; print("ok")'

echo "6) ensure-contracts can inject default contracts"
wg add "No contract" --id no-contract >/dev/null

./.workgraph/speedrift --dir "$TMPDIR" ensure-contracts --apply >/dev/null
wg show --dir "$TMPDIR/.workgraph" no-contract --json | python3 -c 'import json,sys; t=json.load(sys.stdin); desc=t.get("description") or ""; assert "```wg-contract" in desc; print("ok")'

echo "e2e_smoke: OK"
