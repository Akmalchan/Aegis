#!/usr/bin/env bash
# Semgrep on AEGIS itself (the judge asked). Registry packs + our own rules, intentionally vulnerable demo variants and
# rule test fixtures excluded. Exit 1 on any ERROR-severity finding; WARNING/INFO are printed and tolerated.
# Usage: tests/self_audit.sh [--full]   (--full also prints the WARNING/INFO lines)
set -uo pipefail
cd "$(dirname "$0")/.."
SEMGREP="${SEMGREP_BIN:-$(command -v semgrep || echo "$HOME/.local/bin/semgrep")}"
OUT="${SELF_AUDIT_JSON:-state/self-audit.json}"
mkdir -p "$(dirname "$OUT")"
"$SEMGREP" scan --metrics=off --quiet --json \
  --config p/security-audit --config p/secrets --config p/python --config p/javascript --config p/typescript \
  --config rules/ \
  --exclude '_variants' --exclude 'demo/' --exclude 'rules/tests' --exclude 'docs/round2-drafts' --exclude 'build' \
  --exclude 'node_modules' --exclude '.venv' --exclude '.cache' --exclude 'state' \
  . > "$OUT" 2> "$OUT.err"
rc=$?
if [ ! -s "$OUT" ]; then echo "semgrep produced no JSON (exit $rc):"; tail -5 "$OUT.err"; exit 2; fi
python3 -I - "$OUT" "${1:-}" <<'PY'
import json, sys
from collections import Counter
d = json.load(open(sys.argv[1])); full = sys.argv[2] == "--full"
res = d.get("results", []); errs = d.get("errors", [])
by_sev = Counter(r["extra"]["severity"] for r in res)
print(f"self-audit: {len(res)} findings  ERROR={by_sev.get('ERROR',0)} WARNING={by_sev.get('WARNING',0)} INFO={by_sev.get('INFO',0)}  "
      f"files scanned={len(d.get('paths',{}).get('scanned',[]))}  semgrep errors={len(errs)}")
for e in errs:
    print("  semgrep error:", str(e.get("message", e))[:160])
for r in sorted(res, key=lambda r: ({"ERROR":0,"WARNING":1}.get(r["extra"]["severity"], 2), r["path"], r["start"]["line"])):
    sev = r["extra"]["severity"]
    if sev == "ERROR" or full:
        print(f"  {sev:7} {r['path']}:{r['start']['line']}  {r['check_id'].split('.')[-1]}")
sys.exit(1 if by_sev.get("ERROR", 0) else 0)
PY
