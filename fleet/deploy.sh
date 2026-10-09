#!/usr/bin/env bash
# AEGIS fleet deploy: one Guild agent per entry in fleet.json (from guild-agent/sentinel), plus
# guild-agent/warden on a cron. For every repo an agent owns: push + pull_request webhook triggers.
#
#   OWNER=andriidrok1 fleet/deploy.sh                      # real run (guild auth login first)
#   DRY=1 OWNER=andriidrok1 fleet/deploy.sh                # print every command, create nothing
#   ONLY_TRIGGERS=1 ONLY_AGENT=aegis-sentinel-01 ONLY_REPO=andriidrok1/aegis-demo-target OWNER=.. fleet/deploy.sh
#
# Vars: OWNER (required) | WORKSPACE (default aegis; "name" or "owner~name") | SCANNER_INTEGRATION
#       (default $OWNER~aegis-scanner) | WARDEN_NAME (aegis-warden) | DRY | SKIP_NPM | ONLY_TRIGGERS
#       (skip init/rsync/npm/save, just publish+workspace-add+triggers) | ONLY_AGENT | ONLY_REPO
# Verified against guild CLI 0.27.1: `guild --mode json <cmd>` (global flag, before the subcommand)
# returns rc=0 even on failure with {"success":false,...}, so every JSON call goes through gj().
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FLEET="$ROOT/fleet.json"; BUILD="$ROOT/build"; TRIGGERS_OUT="$ROOT/fleet/triggers.json"
TEMPLATE="$ROOT/guild-agent/sentinel"; WARDEN_SRC="$ROOT/guild-agent/warden"

OWNER="${OWNER:?set OWNER=<guild owner>, e.g. OWNER=andriidrok1}"
WORKSPACE="${WORKSPACE:-aegis}"; [[ "$WORKSPACE" == *~* ]] || WORKSPACE="$OWNER~$WORKSPACE"
SCANNER_INTEGRATION="${SCANNER_INTEGRATION:-$OWNER~aegis-scanner}"
WARDEN_NAME="${WARDEN_NAME:-aegis-warden}"
DRY="${DRY:-0}"; SKIP_NPM="${SKIP_NPM:-0}"; ONLY_TRIGGERS="${ONLY_TRIGGERS:-0}"
ONLY_AGENT="${ONLY_AGENT:-}"; ONLY_REPO="${ONLY_REPO:-}"

log()  { printf '\033[1;34m[deploy]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[deploy]\033[0m %s\n' "$*" >&2; }
show() { printf '+'; printf ' %q' "$@"; printf '\n'; }
run()  { show "$@"; [[ "$DRY" == "1" ]] || "$@"; }
# gj: guild in JSON mode. Prints the JSON on stdout, returns 1 (with the error on stderr) when
# the CLI answers {"success":false}. Under DRY only the command is shown and "{}" returned.
gj() {
  show guild --mode json "$@" >&2
  [[ "$DRY" == "1" ]] && { echo '{}'; return 0; }
  local out; out="$(guild --mode json "$@" 2>&1 || true)"   # JSON errors land on stderr when piped
  printf '%s' "$out" | python3 -c 'import json,sys
raw=sys.stdin.read()
try: d=json.loads(raw)
except Exception: print("non-JSON output: "+raw[:300], file=sys.stderr); sys.exit(1)
if isinstance(d,dict) and d.get("success") is False: print("guild: "+str(d.get("error")), file=sys.stderr); sys.exit(1)
print(raw)'
}
jpy() { python3 -c "import json,sys; d=json.load(open(sys.argv[1])); $1" "$FLEET" "${@:2}"; }

[[ -f "$FLEET" ]] || { warn "missing $FLEET"; exit 1; }
command -v python3 >/dev/null || { warn "python3 is required"; exit 1; }
fleet_agents()      { jpy 'print(*d["agents"],sep="\n")'; }
fleet_repos_of()    { jpy 'print(*d["agents"][sys.argv[2]],sep="\n")' "$1"; }
fleet_all_repos()   { jpy 'print(*[r for rs in d["agents"].values() for r in rs],sep="\n")'; }
fleet_warden_cron() { jpy 'print(d.get("warden",{}).get("cron","*/30 * * * *"))'; }
fleet_repos_json()  { jpy 'print(json.dumps({"repos":[r for rs in d["agents"].values() for r in rs]},separators=(",",":")))'; }

# ---------- existing triggers: {"items":[{"name":...,"id":...},...]} ----------
EXISTING_TRIGGERS=""
load_existing_triggers() {
  local raw; raw="$(gj trigger list --workspace "$WORKSPACE" --limit 200)" || { warn "could not list triggers (not logged in?)"; raw='{}'; }
  EXISTING_TRIGGERS="$(printf '%s' "$raw" | python3 -c 'import json,sys
d=json.load(sys.stdin); print(*[i.get("name","") for i in d.get("items",[])],sep="\n")')"
  log "existing triggers in $WORKSPACE: $(printf '%s' "$EXISTING_TRIGGERS" | grep -c . || true)"
}
trigger_exists() { printf '%s\n' "$EXISTING_TRIGGERS" | grep -qxF "$1"; }

record_trigger() {  # $1 name $2 agent $3 repo $4 event $5 json from `trigger create`
  [[ "$DRY" == "1" ]] && return 0
  python3 - "$TRIGGERS_OUT" "$@" <<'PY'
import json,sys,os,datetime
out,name,agent,repo,event,raw=sys.argv[1:7]
d=json.loads(raw); tid=d.get("id") or (d.get("trigger") or {}).get("id")
doc={"triggers":[]}
if os.path.exists(out):
    try: doc=json.load(open(out))
    except Exception: pass
doc["triggers"]=[t for t in doc.get("triggers",[]) if t.get("name")!=name]
doc["triggers"].append({"name":name,"id":tid,"agent":agent,"repo":repo,"event":event,
    "created_at":datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")})
json.dump(doc,open(out,"w"),indent=2); open(out,"a").write("\n")
PY
}

# ---------- build + save one agent ----------
build_agent() {  # $1 agent name, $2 template dir
  local agent="$1" src="$2" dir="$BUILD/$1" f
  [[ -d "$src" ]] || { warn "$agent: template $src missing, skipping build"; return 1; }
  run mkdir -p "$dir"
  if [[ -f "$dir/guild.json" ]]; then log "$agent: $dir/guild.json exists, skipping init"
  else run guild agent init --name "$agent" --agent-type GUILD_TYPESCRIPT --template LLM \
         --category development --directory "$dir" --owner "$OWNER"; fi
  # Keep everything the scaffold owns (guild.json, .guild/, .npmrc, package*.json with the
  # build/bundle scripts, tsconfig). Only our sources go on top.
  run rsync -a --exclude node_modules --exclude .git --exclude dist --exclude guild.json --exclude .guild \
      --exclude .npmrc --exclude 'package*.json' --exclude 'tsconfig*.json' --exclude offline-types "$src/" "$dir/"
  for f in agent.ts fleet-manifest.yaml README.md; do
    [[ -f "$dir/$f" || "$DRY" == "1" ]] || continue
    run sed -i -e "s/__AGENT_NAME__/$agent/g" -e "s/__OWNER__/$OWNER/g" -e "s/__SCANNER_INTEGRATION__/$SCANNER_INTEGRATION/g" "$dir/$f"
  done
  if [[ "$SKIP_NPM" != "1" ]]; then
    # Scanner integration 1.1.0+ (scan_diff, scan_full, record_action, set_status, verify_fix, fleet_insights).
    show "(cd $dir && npm install --silent --save @guildai/agents-sdk@^0.7.8 @guildai-services/$SCANNER_INTEGRATION@^1.1.0)"
    [[ "$DRY" == "1" ]] || (cd "$dir" && npm install --silent --save "@guildai/agents-sdk@^0.7.8" "@guildai-services/$SCANNER_INTEGRATION@^1.1.0")
    # Sentinel only: the sub-agents are PUBLISHED Guild agents, each an npm package @guildai/<owner>~<name> with a
    # ./tool export (guild-agent/SUBAGENTS.md). They must be published before this install can resolve them.
    if [[ "$src" == "$TEMPLATE" ]]; then
      show "(cd $dir && npm install --silent --save @guildai/$OWNER~aegis-triage@^1.0.4 @guildai/$OWNER~aegis-remediator@^1.0.4 @guildai/$OWNER~aegis-verifier@^1.0.4)"
      [[ "$DRY" == "1" ]] || (cd "$dir" && npm install --silent --save "@guildai/$OWNER~aegis-triage@^1.0.4" "@guildai/$OWNER~aegis-remediator@^1.0.4" "@guildai/$OWNER~aegis-verifier@^1.0.4")
    fi
  fi
  show "(cd $dir && guild agent save --message 'deploy $(date +%H:%M)' --publish)"
  [[ "$DRY" == "1" ]] || (cd "$dir" && guild agent save --message "deploy $(date +%H:%M)" --publish)
}

# ---------- published + member of the workspace (both required before any trigger) ----------
ensure_workspace_agent() {
  local full="$OWNER~$1" info
  info="$(gj agent get "$full")" || { warn "$full does not exist on Guild"; return 1; }
  if [[ "$(printf '%s' "$info" | python3 -c 'import json,sys; print(bool(json.load(sys.stdin).get("latest_published_version")))')" == "False" ]]; then
    run guild agent publish "$full" --wait
  fi
  if ! gj workspace agent list --workspace "$WORKSPACE" --limit 200 | grep -q "\"full_name\": *\"$full\""; then
    run guild workspace agent add "$full" --workspace "$WORKSPACE"
  fi
}

declare -A CREATED=() SKIPPED=(); FAILED=0
make_webhook_triggers() {
  local agent="$1" full="$OWNER~$1" repo ev tname out
  CREATED[$agent]=0; SKIPPED[$agent]=0
  while IFS= read -r repo; do
    [[ -z "$repo" || ( -n "$ONLY_REPO" && "$repo" != "$ONLY_REPO" ) ]] && continue
    for ev in push pull_request; do
      tname="${agent}--${repo%%/*}--${repo#*/}--${ev}"
      if trigger_exists "$tname"; then log "trigger exists: $tname"; SKIPPED[$agent]=$(( SKIPPED[$agent] + 1 )); continue; fi
      # service_config key "repo" = guildai~github webhook_config.service_config_fields[0].key
      out="$(gj trigger create --workspace "$WORKSPACE" --type webhook --integration github --event "$ev" \
             --agent "$full" --name "$tname" --service-config "{\"repo\":\"$repo\"}")" || { warn "failed: $tname"; FAILED=$(( FAILED + 1 )); continue; }
      record_trigger "$tname" "$agent" "$repo" "$ev" "$out"; CREATED[$agent]=$(( CREATED[$agent] + 1 ))
    done
  done < <(fleet_repos_of "$agent")
}

# ---------- main ----------
log "owner=$OWNER workspace=$WORKSPACE scanner=$SCANNER_INTEGRATION dry=$DRY only_triggers=$ONLY_TRIGGERS"
load_existing_triggers
while IFS= read -r agent; do
  [[ -z "$agent" || ( -n "$ONLY_AGENT" && "$agent" != "$ONLY_AGENT" ) ]] && continue
  log "=== $agent ==="
  [[ "$ONLY_TRIGGERS" == "1" ]] || build_agent "$agent" "$TEMPLATE" || { warn "$agent: build failed, no triggers"; continue; }
  ensure_workspace_agent "$agent" || continue
  make_webhook_triggers "$agent"
done < <(fleet_agents)

WARDEN_DONE="skipped"
if [[ -z "$ONLY_AGENT" || "$ONLY_AGENT" == "$WARDEN_NAME" ]]; then
  log "=== $WARDEN_NAME ==="
  CRON="$(fleet_warden_cron)"; tname="${WARDEN_NAME}--fleet--all--cron"; CREATED[$WARDEN_NAME]=0; SKIPPED[$WARDEN_NAME]=0
  if { [[ "$ONLY_TRIGGERS" == "1" ]] || build_agent "$WARDEN_NAME" "$WARDEN_SRC"; } && ensure_workspace_agent "$WARDEN_NAME"; then
    if trigger_exists "$tname"; then log "trigger exists: $tname"; SKIPPED[$WARDEN_NAME]=1
    elif out="$(gj trigger create --workspace "$WORKSPACE" --type time --frequency CRON --cron-expression "$CRON" \
          --cron-timezone America/Los_Angeles --agent "$OWNER~$WARDEN_NAME" --name "$tname" --input "$(fleet_repos_json)")"; then
      record_trigger "$tname" "$WARDEN_NAME" "*" "cron" "$out"; CREATED[$WARDEN_NAME]=1
    else warn "failed: $tname"; FAILED=$(( FAILED + 1 )); fi
    WARDEN_DONE="cron '$CRON'"
  fi
fi

echo; printf '%-20s %-6s %-8s %-9s %s\n' AGENT REPOS CREATED EXISTING REPOS_LIST
while IFS= read -r agent; do
  printf '%-20s %-6s %-8s %-9s %s\n' "$agent" "$(fleet_repos_of "$agent" | grep -c .)" "${CREATED[$agent]:-0}" "${SKIPPED[$agent]:-0}" "$(fleet_repos_of "$agent" | paste -sd, -)"
done < <(fleet_agents)
printf '%-20s %-6s %-8s %-9s %s\n' "$WARDEN_NAME" "$(fleet_all_repos | grep -c .)" "${CREATED[$WARDEN_NAME]:-0}" "${SKIPPED[$WARDEN_NAME]:-0}" "$WARDEN_DONE"
log "full fleet = $(( $(fleet_all_repos | grep -c .) * 2 )) webhook triggers + 1 cron. Verify: guild --mode json trigger list --workspace $WORKSPACE --limit 200"
[[ "$DRY" == "1" ]] && log "DRY run: nothing executed, triggers.json untouched." || log "trigger ids recorded in $TRIGGERS_OUT"
[[ "$FAILED" == "0" ]] || { warn "$FAILED trigger(s) failed (GitHub not connected? app.guild.ai > Credentials > GitHub > Connect, then re-run: existing triggers are skipped)"; exit 1; }
