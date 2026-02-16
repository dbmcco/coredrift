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
mkdir -p "$TMPDIR/.workgraph/executors"
cat > "$TMPDIR/.workgraph/executors/custom.toml" <<'TOML'
[executor]
type = "claude"
command = "claude"
args = ["--print"]

[executor.prompt_template]
template = """
## Coredrift Protocol
- Treat the `wg-contract` block (in the task description) as binding.
- At start and just before completion, run:
  ./.workgraph/coredrift check --task {{task_id}} --write-log --create-followups
"""
TOML

UXDRIFT_DUMMY="$TMPDIR/uxdrift-dummy"
export UXDRIFT_E2E_MARKER="$TMPDIR/uxdrift-called.txt"
cat > "$UXDRIFT_DUMMY" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
echo "uxdrift $*" >> "${UXDRIFT_E2E_MARKER:?}"
exit 0
SH
chmod +x "$UXDRIFT_DUMMY"

"$ROOT/bin/coredrift" --dir "$TMPDIR" install --uxdrift-bin "$UXDRIFT_DUMMY" >/dev/null
test -x "$TMPDIR/.workgraph/coredrift"
test -x "$TMPDIR/.workgraph/drifts"
test -x "$TMPDIR/.workgraph/uxdrift"
rg -n "## Coredrift Protocol" "$TMPDIR/.workgraph/executors/claude.toml" >/dev/null
rg -n "## Coredrift Protocol" "$TMPDIR/.workgraph/executors/custom.toml" >/dev/null
rg -n "\\./\\.workgraph/drifts check" "$TMPDIR/.workgraph/executors/custom.toml" >/dev/null
rg -n "^\\.coredrift/$" "$TMPDIR/.workgraph/.gitignore" >/dev/null
rg -n "## uxdrift Protocol" "$TMPDIR/.workgraph/executors/claude.toml" >/dev/null
rg -n "## uxdrift Protocol" "$TMPDIR/.workgraph/executors/custom.toml" >/dev/null
rg -n "^\\.uxdrift/$" "$TMPDIR/.workgraph/.gitignore" >/dev/null

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

echo "1) coredrift check reports findings"
set +e
REPORT="$(./.workgraph/coredrift --dir "$TMPDIR" check --json)"
CODE="$?"
set -e
if [[ "$CODE" -ne 0 && "$CODE" -ne 3 ]]; then
  echo "error: coredrift check failed with exit code $CODE" >&2
  exit "$CODE"
fi
python3 -c 'import json,sys; r=json.loads(sys.stdin.read()); kinds={f["kind"] for f in r.get("findings", [])}; assert "scope_drift" in kinds, kinds; assert "hardening_in_core" in kinds, kinds; print("ok")' <<<"$REPORT"

echo "2) coredrift can write wg log and create follow-up tasks"
set +e
./.workgraph/coredrift --dir "$TMPDIR" check --write-log --create-followups >/dev/null
CODE="$?"
set -e
if [[ "$CODE" -ne 0 && "$CODE" -ne 3 ]]; then
  echo "error: coredrift check (write-log/followups) failed with exit code $CODE" >&2
  exit "$CODE"
fi

wg show --dir "$TMPDIR/.workgraph" core-task --json | python3 -c 'import json,sys; t=json.load(sys.stdin); msgs=[e.get("message","") for e in t.get("log",[])]; assert any(m.startswith("Coredrift:") for m in msgs), msgs; print("ok")'

wg show --dir "$TMPDIR/.workgraph" drift-harden-core-task --json >/dev/null
wg show --dir "$TMPDIR/.workgraph" drift-scope-core-task --json >/dev/null

echo "2b) drifts wrapper can run unified check"
test ! -e "$UXDRIFT_E2E_MARKER"
set +e
./.workgraph/drifts --dir "$TMPDIR" check --task core-task --write-log --create-followups >/dev/null
CODE="$?"
set -e
if [[ "$CODE" -ne 0 && "$CODE" -ne 3 ]]; then
  echo "error: drifts check failed with exit code $CODE" >&2
  exit "$CODE"
fi
test ! -e "$UXDRIFT_E2E_MARKER"
echo "ok"

echo "2c) drifts runs uxdrift when a task declares a uxdrift spec"
UX_DESC_FILE="$(mktemp)"
cat > "$UX_DESC_FILE" <<'MD'
```wg-contract
schema = 1
mode = "core"
objective = "ux task"
non_goals = ["No fallbacks"]
touch = ["src/**"]
acceptance = []
max_files = 10
max_loc = 200
auto_followups = true
```

```uxdrift
schema = 1
url = "http://localhost:12345"
pages = ["/"]
llm = false
```

Run uxdrift.
MD

wg add "UX task" --id ux-task -d "$(cat "$UX_DESC_FILE")" >/dev/null
wg claim ux-task --actor tester >/dev/null

set +e
./.workgraph/drifts --dir "$TMPDIR" check --task ux-task --write-log --create-followups >/dev/null
CODE="$?"
set -e
if [[ "$CODE" -ne 0 && "$CODE" -ne 3 ]]; then
  echo "error: drifts check (uxdrift) failed with exit code $CODE" >&2
  exit "$CODE"
fi
test -s "$UXDRIFT_E2E_MARKER"
echo "ok"

echo "3) pit-stop escalation after consecutive drift"
set +e
./.workgraph/coredrift --dir "$TMPDIR" check --task core-task --create-followups >/dev/null
CODE="$?"
set -e
if [[ "$CODE" -ne 0 && "$CODE" -ne 3 ]]; then
  echo "error: coredrift check (pit-stop) failed with exit code $CODE" >&2
  exit "$CODE"
fi
wg show --dir "$TMPDIR/.workgraph" coredrift-pit-core-task --json >/dev/null
echo "ok"

echo "4) monitor+redirect pipeline emits events and applies actions"
wg add "Core task 2" --id core-task-2 -d "$(cat "$DESC_FILE")" >/dev/null
wg claim core-task-2 --actor tester >/dev/null
echo "readme2" > README2.md
printf '\n# fallback added\n' >> src/app.py

./.workgraph/coredrift --dir "$TMPDIR" monitor --once >/dev/null
test -s "$TMPDIR/.workgraph/.coredrift/events.jsonl"

set +e
./.workgraph/coredrift --dir "$TMPDIR" redirect --once --write-log --create-followups --from-start >/dev/null
CODE="$?"
set -e
if [[ "$CODE" -ne 0 && "$CODE" -ne 3 ]]; then
  echo "error: coredrift redirect failed with exit code $CODE" >&2
  exit "$CODE"
fi
wg show --dir "$TMPDIR/.workgraph" drift-harden-core-task-2 --json >/dev/null
wg show --dir "$TMPDIR/.workgraph" drift-scope-core-task-2 --json >/dev/null
echo "ok"

echo "5) contract set-touch rewrites description"
./.workgraph/coredrift --dir "$TMPDIR" contract set-touch --task core-task "src/**" "tests/**" >/dev/null
wg show --dir "$TMPDIR/.workgraph" core-task --json | python3 -c 'import json,sys; t=json.load(sys.stdin); d=t.get("description") or ""; assert "tests/**" in d, d; print("ok")'

echo "6) ensure-contracts can inject default contracts"
wg add "No contract" --id no-contract >/dev/null

./.workgraph/coredrift --dir "$TMPDIR" ensure-contracts --apply >/dev/null
wg show --dir "$TMPDIR/.workgraph" no-contract --json | python3 -c 'import json,sys; t=json.load(sys.stdin); desc=t.get("description") or ""; assert "```wg-contract" in desc; print("ok")'

echo "e2e_smoke: OK"
