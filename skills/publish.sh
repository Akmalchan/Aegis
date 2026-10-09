#!/usr/bin/env bash
# Publish the AEGIS knowledge skills to Guild (guild skill create + skill version create, CLI 0.27.1).
# Agents reach them through skillsTools; the "Use when ..." description decides activation, keep it precise.
#   OWNER=andriidrok1 skills/publish.sh            # idempotent: skips `skill create` when the skill exists
#   DRY=1 OWNER=x skills/publish.sh                # print only
# Vars: OWNER (required), SKILL_VERSION (default 1.0.0; bump it to push a new body), PUBLIC=1 adds --public.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OWNER="${OWNER:?set OWNER=<guild owner>}"; SKILL_VERSION="${SKILL_VERSION:-1.0.0}"
DRY="${DRY:-0}"; PUB=(); [[ "${PUBLIC:-0}" == "1" ]] && PUB=(--public)
run() { printf '+'; printf ' %q' "$@"; printf '\n'; [[ "$DRY" == "1" ]] || "$@"; }

EXISTING="$(guild --mode json skill list --owner "$OWNER" --limit 100 2>/dev/null | python3 -c '
import json,sys; print(*[i["full_name"] for i in json.load(sys.stdin).get("items",[])],sep="\n")' 2>/dev/null || true)"

publish_skill() {  # $1 name  $2 overview  $3 "Use when ..." description  $4 body file
  local name="$1" overview="$2" desc="$3" body="$4"
  [[ -f "$body" ]] || { echo "missing $body" >&2; return 1; }
  if printf '%s\n' "$EXISTING" | grep -qxF "$OWNER~$name"; then echo "skill $OWNER~$name exists, skipping create"
  else run guild skill create "$OWNER~$name" --owner "$OWNER" --overview "$overview" "${PUB[@]}"; fi
  run guild skill version create "$OWNER~$name" --owner "$OWNER" --version-number "$SKILL_VERSION" --description "$desc" --body-file "$body"
}

publish_skill security-review \
  "How AEGIS judges a Semgrep finding: true positive test, severity by CWE, GitHub Issue template with the AEGIS-FP footer." \
  "Use when you hold a Semgrep finding and must decide if it is a real vulnerability, assign severity, or write the [AEGIS] GitHub Issue." \
  "$HERE/security-review.md"
publish_skill remediation-playbook \
  "How AEGIS patches a confirmed finding: PR vs Issue-only, minimal diff, Semgrep fix strings, parameterised SQL, env secrets, PR body." \
  "Use when a finding is confirmed and you must decide whether to open a fix PR, apply a Semgrep fix, or write parameterised SQL / env-var / subprocess patches." \
  "$HERE/remediation-playbook.md"

echo; echo "skills: $OWNER~security-review@$SKILL_VERSION, $OWNER~remediation-playbook@$SKILL_VERSION"
echo "verify: guild skill list --owner $OWNER"
