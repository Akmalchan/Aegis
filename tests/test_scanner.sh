#!/usr/bin/env bash
# End-to-end test of the scanner contract (openapi.yaml) against a real server + real Semgrep.
#
# Server: $AEGIS_URL if set; else http://127.0.0.1:8787 if it answers /healthz; else this script
# starts its own uvicorn on a random free port and kills it on exit.
# Builds a throwaway git repo:  A = demo-target/app.py (clean)  ->  B = app_vulnerable.py  ->  C = app_fixed.py
# and scans it as repo "local:<tmpdir>".
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

env_get() {
  [ -f "$ROOT/.env" ] || return 0
  grep -E "^[[:space:]]*$1=" "$ROOT/.env" | tail -1 | cut -d= -f2- \
    | sed -E 's/[[:space:]]+#.*$//; s/^[[:space:]]+//; s/[[:space:]]+$//; s/^"(.*)"$/\1/'
}
KEY="${SCANNER_KEY:-$(env_get SCANNER_KEY)}"
[ -n "$KEY" ] || { echo "SCANNER_KEY not set (env or .env)"; exit 2; }

PASS=0; FAIL=0
pass() { echo "PASS  $*"; PASS=$((PASS + 1)); }
fail() { echo "FAIL  $*"; FAIL=$((FAIL + 1)); }

TMP="$(mktemp -d "${TMPDIR:-/tmp}/aegis-test.XXXXXX")"
TMP="$(cd "$TMP" && pwd -P)"
SERVER_PID=""
kill_tree() { local k; for k in $(pgrep -P "$1" 2>/dev/null); do kill_tree "$k"; done; kill "$1" 2>/dev/null || true; }
cleanup() {
  [ -n "$SERVER_PID" ] && kill_tree "$SERVER_PID"
  # scanner.checkout clones local:<path> into .cache/local__<sha1(path)[:10]>
  local h; h="$(printf '%s' "$TMP" | shasum | cut -c1-10)"
  rm -rf "$TMP" "$ROOT/.cache/local__$h"
}
trap cleanup EXIT

# ---------------------------------------------------------------- server
healthy() { curl -s -m 5 "$1/healthz" | grep -q '"ok":[[:space:]]*true'; }
if [ -n "${AEGIS_URL:-}" ]; then
  BASE="${AEGIS_URL%/}"
elif healthy http://127.0.0.1:8787; then
  BASE=http://127.0.0.1:8787
else
  PORT="$(python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1])')"
  BASE="http://127.0.0.1:$PORT"
  echo "starting test server on $BASE (log: $TMP/server.log)"
  uv run uvicorn aegis.server:app --host 127.0.0.1 --port "$PORT" > "$TMP/server.log" 2>&1 &
  SERVER_PID=$!
  for _ in $(seq 90); do healthy "$BASE" && break; kill -0 "$SERVER_PID" 2>/dev/null || break; sleep 1; done
fi
if healthy "$BASE"; then pass "GET /healthz -> ok ($(curl -s "$BASE/healthz"))"
else
  fail "GET /healthz at $BASE"; [ -f "$TMP/server.log" ] && tail -30 "$TMP/server.log"; exit 1
fi

# ---------------------------------------------------------------- fixture repo
R="$TMP/repo"; mkdir -p "$R"
g() { git -C "$R" -c user.name=aegis-test -c user.email=test@aegis.local -c commit.gpgsign=false "$@"; }
g init -q -b main
cp demo-target/app.py "$R/app.py";                       g add -A; g commit -qm "A clean";      A="$(g rev-parse HEAD)"
cp demo-target/_variants/app_vulnerable.py "$R/app.py";  g add -A; g commit -qm "B vulnerable"; B="$(g rev-parse HEAD)"
cp demo-target/_variants/app_fixed.py "$R/app.py";       g add -A; g commit -qm "C fixed";      C="$(g rev-parse HEAD)"
REPO="local:$R"
echo "fixture repo $R  A=${A:0:7} B=${B:0:7} C=${C:0:7}"

# ---------------------------------------------------------------- helpers
# call METHOD PATH [BODY] [KEY]  -> sets CODE and BODY_OUT
call() {
  local method="$1" path="$2" body="${3:-}" key="${4-$KEY}" args=()
  [ -n "$key" ] && args+=(-H "X-AEGIS-Key: $key")
  [ -n "$body" ] && args+=(-H 'content-type: application/json' --data "$body")
  BODY_OUT="$(curl -s -m 600 -X "$method" ${args[@]+"${args[@]}"} -w $'\n%{http_code}' "$BASE$path")"
  CODE="${BODY_OUT##*$'\n'}"; BODY_OUT="${BODY_OUT%$'\n'*}"
}
# check NAME PY_EXPR : evaluates PY_EXPR with d = parsed BODY_OUT, code = HTTP status
check() {
  local name="$1" expr="$2" out
  if out="$(printf '%s' "$BODY_OUT" | python3 -c '
import json, sys
code = int(sys.argv[2]); raw = sys.stdin.read()
try: d = json.loads(raw)
except Exception: d = None
ok = False
try: ok = bool(eval(sys.argv[1]))
except Exception as e: print("expr error:", e)
if not ok:
    print("HTTP", code, raw[:600]); sys.exit(1)
' "$expr" "$CODE" 2>&1)"; then pass "$name"; else fail "$name"; echo "$out" | sed 's/^/      /'; fi
}
summary() {  # one-line summary of a ScanResult
  printf '%s' "$BODY_OUT" | python3 -c '
import json, sys
try:
    d = json.load(sys.stdin)
    print("      verdict=%s n=%d n_files=%s ms=%s rules=%s" % (d.get("verdict"), len(d.get("findings", [])), d.get("n_files"), d.get("ms"),
          sorted(f["rule_id"].split(".")[-1] + ("[fix]" if f.get("fix") else "") for f in d.get("findings", []))))
except Exception as e: print("      (unparseable)", e)'
}
SHAPE='code == 200 and {"repo","sha","verdict","findings","ms"} <= d.keys() and d["verdict"] in ("safe","unsafe") and isinstance(d["findings"], list) and all({"rule_id","path","start_line","end_line","lines","message","severity","fingerprint"} <= f.keys() and f["severity"] in ("ERROR","WARNING","INFO") for f in d["findings"])'

# ---------------------------------------------------------------- auth
call POST /scan/diff "{\"repo\":\"$REPO\",\"base_sha\":\"$A\",\"head_sha\":\"$B\",\"agent\":\"test\"}" ""
check "POST /scan/diff without X-AEGIS-Key -> 401" 'code == 401'
call POST /scan/diff "{\"repo\":\"$REPO\",\"base_sha\":\"$A\",\"head_sha\":\"$B\",\"agent\":\"test\"}" "wrong-key"
check "POST /scan/diff with wrong key -> 401" 'code == 401'
call GET "/insights?hours=24" "" ""
check "GET /insights without key -> 401" 'code == 401'

# ---------------------------------------------------------------- scans
call POST /scan/diff "{\"repo\":\"$REPO\",\"base_sha\":\"$A\",\"head_sha\":\"$B\",\"agent\":\"test\"}"
check "scan/diff A->B: response matches ScanResult schema" "$SHAPE"
check "scan/diff A->B: verdict unsafe, >=2 findings" 'd["verdict"] == "unsafe" and len(d["findings"]) >= 2'
check "scan/diff A->B: sha/base_sha echo head/base" "d['sha'] == '$B' and d.get('base_sha') == '$A'"
check "scan/diff A->B: fingerprints are 12 hex chars" 'all(len(f["fingerprint"]) == 12 and all(c in "0123456789abcdef" for c in f["fingerprint"]) for f in d["findings"])'
summary
printf '%s' "$BODY_OUT" | python3 -c 'import json,sys; d=json.load(sys.stdin); n=sum(1 for f in d["findings"] if f.get("fix")); print("INFO  scan/diff A->B: %d finding(s) carry a fix (B.md target: >=2)" % n)' 2>/dev/null

call POST /scan/diff "{\"repo\":\"$REPO\",\"base_sha\":\"$B\",\"head_sha\":\"$C\",\"agent\":\"test\"}"
check "scan/diff B->C: response matches ScanResult schema" "$SHAPE"
check "scan/diff B->C: verdict safe, 0 findings" 'd["verdict"] == "safe" and len(d["findings"]) == 0'
summary

call POST /scan/full "{\"repo\":\"$REPO\",\"sha\":\"$B\",\"agent\":\"test\"}"
check "scan/full B: response matches ScanResult schema" "$SHAPE"
check "scan/full B: verdict unsafe, >=2 findings" 'd["verdict"] == "unsafe" and len(d["findings"]) >= 2'
summary

call POST /scan/full "{\"repo\":\"$REPO\",\"sha\":\"$A\",\"agent\":\"test\"}"
check "scan/full A (clean): verdict safe" 'code == 200 and d["verdict"] == "safe"'
summary

# ---------------------------------------------------------------- actions / insights
call POST /actions "{\"agent\":\"test\",\"repo\":\"$REPO\",\"kind\":\"status_set\",\"ref\":\"$B\",\"latency_ms\":1234}"
check "POST /actions kind=status_set -> {ok:true}" 'code == 200 and d == {"ok": True} or (code == 200 and d.get("ok") is True)'
call POST /actions "{\"agent\":\"test\",\"repo\":\"$REPO\",\"kind\":\"not_a_kind\",\"ref\":\"$B\"}"
check "POST /actions bad kind -> 422" 'code == 422'
call POST /actions "{\"agent\":\"test\",\"repo\":\"$REPO\",\"kind\":\"issue_opened\"}"
check "POST /actions missing ref -> 422" 'code == 422'

call GET "/insights?hours=24"
check "GET /insights -> 200 with rising_repos/noisy_rules/reopened/agent_latency lists" \
  'code == 200 and all(isinstance(d.get(k), list) for k in ("rising_repos","noisy_rules","reopened","agent_latency"))'

echo
echo "== $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
