#!/usr/bin/env bash
# Publish the AEGIS scanner as a Guild custom integration ("aegis-scanner").
#
# !!! The integration's base URL is FROZEN at publish time. If the tunnel URL changes
# !!! (quick tunnel restarted: `./run.sh tunnel`, laptop reboot, cloudflared crash), the published
# !!! version points at a dead host: bump VERSION (e.g. VERSION=1.0.1) and re-run this script,
# !!! or delete/recreate the integration. Keep the tunnel alive (./run.sh never restarts it on API restarts).
#
# Env:
#   TUNNEL_URL   public https URL of the scanner (default: state/tunnel_url.txt, written by ./run.sh)
#   OWNER        Guild account/org that owns the integration (default: GUILD_OWNER from .env)
#   SCANNER_KEY  value Guild injects as X-AEGIS-Key (default: SCANNER_KEY from .env)
#   VERSION      integration version number (default 1.0.0)
#   TEST_BODY    JSON body for the final scan_diff test call (default: demo target, full-scan fallback)
#   DRY=1        print the commands, run nothing (skips the healthz preflight)
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
NAME=aegis-scanner

env_get() {
  [ -f "$ROOT/.env" ] || return 0
  grep -E "^[[:space:]]*$1=" "$ROOT/.env" | tail -1 | cut -d= -f2- \
    | sed -E 's/[[:space:]]+#.*$//; s/^[[:space:]]+//; s/[[:space:]]+$//; s/^"(.*)"$/\1/'
}
TUNNEL_URL="${TUNNEL_URL:-$(cat "$ROOT/state/tunnel_url.txt" 2>/dev/null)}"
TUNNEL_URL="${TUNNEL_URL%/}"
OWNER="${OWNER:-$(env_get GUILD_OWNER)}"
SCANNER_KEY="${SCANNER_KEY:-$(env_get SCANNER_KEY)}"
VERSION="${VERSION:-1.0.0}"
DRY="${DRY:-}"
DEFAULT_TEST_BODY='{"repo":"andriidrok1/aegis-demo-target","base_sha":"","head_sha":"HEAD","agent":"integration-test"}'
TEST_BODY="${TEST_BODY:-$DEFAULT_TEST_BODY}"

die() { echo "ERROR: $*" >&2; exit 1; }
[ -n "$TUNNEL_URL" ]  || die "TUNNEL_URL not set and state/tunnel_url.txt missing (run ./run.sh first)"
[ -n "$OWNER" ]       || die "OWNER not set (export OWNER=<guild account>, or GUILD_OWNER in .env)"
[ -n "$SCANNER_KEY" ] || die "SCANNER_KEY not set (export it, or SCANNER_KEY in .env)"
case "$TUNNEL_URL" in https://*) ;; *) die "TUNNEL_URL must be public https (Guild blocks private/loopback URLs): $TUNNEL_URL" ;; esac
ID="$OWNER~$NAME"

# run CMD...: print (key masked), execute unless DRY. Returns the command's status.
run() {
  local shown; shown="$(printf '%q ' "$@")"
  [ "${#SCANNER_KEY}" -ge 6 ] && shown="${shown//$SCANNER_KEY/****}"
  echo "+ $shown"
  [ -n "$DRY" ] && return 0
  "$@"
}
# step LABEL TOLERATE CMD...: TOLERATE=1 => failure (e.g. "already exists") is a warning, not fatal
step() {
  local label="$1" tolerate="$2"; shift 2
  echo; echo "== $label"
  if run "$@"; then return 0; fi
  if [ "$tolerate" = 1 ]; then echo "   (failed; assuming it already exists, continuing)"; return 0; fi
  die "$label failed. If this version already exists, re-run with VERSION=<next> (e.g. 1.0.1)."
}

# --- preflight: the scanner must answer through the public URL before we freeze it into Guild
if [ -z "$DRY" ]; then
  command -v guild >/dev/null || die "guild CLI not installed / not on PATH"
  echo "preflight: GET $TUNNEL_URL/healthz"
  hz="$(curl -s -m 15 --retry 3 --retry-delay 2 --retry-all-errors "$TUNNEL_URL/healthz" || true)"
  echo "$hz" | grep -q '"ok":[[:space:]]*true' \
    || die "scanner not reachable at $TUNNEL_URL/healthz (got: ${hz:0:200}). Start it with ./run.sh and check state/tunnel_url.txt."
  echo "preflight ok: $hz"
else
  echo "DRY=1: printing commands only (preflight skipped)"
fi

# --- spec copy with servers[0].url = tunnel URL
SPEC="$(mktemp "${TMPDIR:-/tmp}/aegis-openapi.XXXXXX")"
mv "$SPEC" "$SPEC.yaml"; SPEC="$SPEC.yaml"
trap 'rm -f "$SPEC"' EXIT
awk -v url="$TUNNEL_URL" '
  /^servers:/ { in_s=1; print; next }
  in_s && /^[^[:space:]]/ { in_s=0 }
  in_s && !done && /- url:/ { sub(/- url:.*/, "- url: " url); done=1 }
  { print }' "$ROOT/openapi.yaml" > "$SPEC"
grep -q -- "- url: $TUNNEL_URL\$" "$SPEC" || die "failed to set servers[0].url in spec copy"
echo "spec: $SPEC (servers[0].url = $TUNNEL_URL)"

step "create integration"   1 guild integration create "$NAME" --base-url "$TUNNEL_URL" --auth-scheme api-key \
  --description "AEGIS: Semgrep scanner for the agent fleet. scan_diff answers 'did this push make the repo unsafe?'; scan_full, record_action, fleet_insights (ClickHouse)."
step "import operations"    1 guild integration operation create "$ID" --openapi "$SPEC"
step "build version"        0 guild integration version build "$ID" --version-number "$VERSION"
step "publish version"      0 guild integration version publish "$ID" --version-number "$VERSION"
step "connect credential"   1 guild integration connect "$ID" --owner "$OWNER" --token "$SCANNER_KEY"
step "test scan_diff"       0 guild integration version test "$ID" --operation scan_diff --input-body "$TEST_BODY"

echo
echo "done: $ID v$VERSION -> $TUNNEL_URL"
echo "REMINDER: base URL is frozen. Do not run ./run.sh tunnel / reboot without bumping VERSION and re-running this."
