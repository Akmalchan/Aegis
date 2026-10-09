#!/usr/bin/env bash
# AEGIS fleet deploy: builds one Guild agent per entry in fleet.json from the
# guild-agent/sentinel template, publishes it, and creates push + pull_request
# webhook triggers for each repo the agent owns. Also deploys guild-agent/warden
# with a single CRON trigger over every fleet repo.
#
# Usage:
#   OWNER=<guild-owner> fleet/deploy.sh            # real run (needs `guild auth login`)
#   DRY=1 OWNER=<guild-owner> fleet/deploy.sh      # print every command, run nothing
#
# Variables:
#   OWNER               required. Guild owner (account) that owns agents + the scanner integration.
#   SCANNER_INTEGRATION default "$OWNER~aegis-scanner". Substituted for __SCANNER_INTEGRATION__.
#   WORKSPACE           optional. Passed as --workspace to trigger commands.
#   WARDEN_NAME         default "aegis-warden".
#   DRY                 1 = print commands instead of running them.
#   SKIP_NPM            1 = skip `npm install` (faster re-deploys).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FLEET="$ROOT/fleet.json"
TEMPLATE="$ROOT/guild-agent/sentinel"
WARDEN_SRC="$ROOT/guild-agent/warden"
BUILD="$ROOT/build"
TRIGGERS_OUT="$ROOT/fleet/triggers.json"

OWNER="${OWNER:?set OWNER=<guild owner>, e.g. OWNER=andriidrok1}"
SCANNER_INTEGRATION="${SCANNER_INTEGRATION:-$OWNER~aegis-scanner}"
WORKSPACE="${WORKSPACE:-}"
WARDEN_NAME="${WARDEN_NAME:-aegis-warden}"
DRY="${DRY:-0}"
SKIP_NPM="${SKIP_NPM:-0}"

WS_FLAG=()
[[ -n "$WORKSPACE" ]] && WS_FLAG=(--workspace "$WORKSPACE")

log()  { printf '\033[1;34m[deploy]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[deploy]\033[0m %s\n' "$*" >&2; }
run() {
  # Print the command; execute it unless DRY=1.
  printf '+'; printf ' %q' "$@"; printf '\n'
  if [[ "$DRY" != "1" ]]; then "$@"; fi
}
# Same as run, but captures stdout (empty under DRY).
run_capture() {
  printf '+' >&2; printf ' %q' "$@" >&2; printf '\n' >&2
  if [[ "$DRY" == "1" ]]; then echo ""; else "$@"; fi
}

[[ -f "$FLEET" ]] || { warn "missing $FLEET"; exit 1; }
command -v python3 >/dev/null || { warn "python3 is required"; exit 1; }

# ---------- read fleet.json ----------
fleet_agents()      { python3 -c 'import json,sys; [print(a) for a in json.load(open(sys.argv[1]))["agents"]]' "$FLEET"; }
fleet_repos_of()    { python3 -c 'import json,sys; [print(r) for r in json.load(open(sys.argv[1]))["agents"][sys.argv[2]]]' "$FLEET" "$1"; }
fleet_all_repos()   { python3 -c 'import json,sys; [print(r) for rs in json.load(open(sys.argv[1]))["agents"].values() for r in rs]' "$FLEET"; }
fleet_warden_cron() { python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("warden",{}).get("cron","*/30 * * * *"))' "$FLEET"; }
fleet_repos_json()  { python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); print(json.dumps({"repos":[r for rs in d["agents"].values() for r in rs]}, separators=(",",":")))' "$FLEET"; }

# ---------- existing triggers (best-effort parse of `guild trigger list`) ----------
EXISTING_TRIGGERS=""
load_existing_triggers() {
  if [[ "$DRY" == "1" ]]; then
    log "DRY: skipping 'guild trigger list' (would check existing trigger names)"
    echo "+ guild --mode json trigger list --limit 200${WORKSPACE:+ --workspace $WORKSPACE}"
    return
  fi
  local raw
  raw="$(guild --mode json trigger list --limit 200 "${WS_FLAG[@]}" 2>/dev/null || true)"
  EXISTING_TRIGGERS="$(printf '%s' "$raw" | python3 -c '
import json,sys,re
raw=sys.stdin.read()
names=set()
try:
    data=json.loads(raw)
    def walk(x):
        if isinstance(x,dict):
            if isinstance(x.get("name"),str): names.add(x["name"])
            for v in x.values(): walk(v)
        elif isinstance(x,list):
            for v in x: walk(v)
    walk(data)
except Exception:
    # text fallback: anything that looks like our naming scheme
    names.update(re.findall(r"[a-z0-9-]+--[A-Za-z0-9_.-]+--[A-Za-z0-9_.-]+--(?:push|pull_request|cron)", raw))
print("\n".join(sorted(names)))
' || true)"
  log "existing triggers: $(printf '%s' "$EXISTING_TRIGGERS" | grep -c . || true)"
}
trigger_exists() { printf '%s\n' "$EXISTING_TRIGGERS" | grep -qxF "$1"; }

# ---------- triggers.json bookkeeping ----------
record_trigger() {
  # $1 name $2 agent $3 repo $4 event $5 raw json output of `guild trigger create`
  [[ "$DRY" == "1" ]] && return 0
  python3 - "$TRIGGERS_OUT" "$1" "$2" "$3" "$4" "$5" <<'PY'
import json,sys,os,datetime
out,name,agent,repo,event,raw=sys.argv[1:7]
tid=None
try:
    d=json.loads(raw)
    def find(x):
        global tid
        if tid: return
        if isinstance(x,dict):
            if isinstance(x.get("id"),str) and x.get("name")==name: tid=x["id"]; return
            for v in x.values(): find(v)
        elif isinstance(x,list):
            for v in x: find(v)
    find(d)
    if tid is None and isinstance(d,dict) and isinstance(d.get("id"),str): tid=d["id"]
except Exception:
    pass
doc={"triggers":[]}
if os.path.exists(out):
    try: doc=json.load(open(out))
    except Exception: pass
doc.setdefault("triggers",[])
doc["triggers"]=[t for t in doc["triggers"] if t.get("name")!=name]
doc["triggers"].append({"name":name,"id":tid,"agent":agent,"repo":repo,"event":event,
                        "created_at":datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")})
json.dump(doc,open(out,"w"),indent=2); open(out,"a").write("\n")
PY
}

# ---------- build + publish one agent from a template dir ----------
build_agent() {
  # $1 agent name, $2 template dir
  local agent="$1" src="$2" dir="$BUILD/$1"
  if [[ ! -d "$src" ]]; then
    if [[ "$DRY" == "1" ]]; then warn "template $src missing (ok under DRY, A1/A3 still writing it)"; else warn "template $src missing"; return 1; fi
  fi
  run mkdir -p "$dir"
  if [[ -f "$dir/guild.json" ]]; then
    log "$agent: build/$agent/guild.json exists, skipping init"
  else
    run guild agent init --name "$agent" --template LLM --directory "$dir" --owner "$OWNER"
  fi
  if [[ -d "$src" ]]; then
    run rsync -a --exclude node_modules --exclude guild.json --exclude .git "$src/" "$dir/"
    local f
    for f in agent.ts guild.yaml package.json README.md; do
      [[ -f "$dir/$f" ]] || { [[ "$DRY" == "1" ]] && echo "+ sed -i 's/__AGENT_NAME__/$agent/g; s/__OWNER__/$OWNER/g; s/__SCANNER_INTEGRATION__/$SCANNER_INTEGRATION/g' $dir/$f  # (file not present yet)"; continue; }
      run sed -i -e "s/__AGENT_NAME__/$agent/g" -e "s/__OWNER__/$OWNER/g" -e "s/__SCANNER_INTEGRATION__/$SCANNER_INTEGRATION/g" "$dir/$f"
    done
  else
    echo "+ rsync -a --exclude node_modules --exclude guild.json $src/ $dir/   # (template missing)"
    echo "+ sed -i 's/__AGENT_NAME__/$agent/g; s/__OWNER__/$OWNER/g; s/__SCANNER_INTEGRATION__/$SCANNER_INTEGRATION/g' $dir/{agent.ts,guild.yaml,package.json}"
  fi
  if [[ "$SKIP_NPM" != "1" ]]; then
    if [[ "$DRY" == "1" ]]; then echo "+ (cd $dir && npm install --silent)"; else (cd "$dir" && npm install --silent); fi
  fi
  local msg="deploy $(date +%H:%M)"
  if [[ "$DRY" == "1" ]]; then
    echo "+ (cd $dir && guild agent save --message '$msg' --wait --publish)"
  else
    (cd "$dir" && guild agent save --message "$msg" --wait --publish)
  fi
}

# ---------- webhook triggers for one agent ----------
declare -A CREATED=() SKIPPED=()
make_webhook_triggers() {
  local agent="$1" full="$OWNER~$1" repo owner name ev tname out
  CREATED[$agent]=0; SKIPPED[$agent]=0
  while IFS= read -r repo; do
    [[ -z "$repo" ]] && continue
    owner="${repo%%/*}"; name="${repo#*/}"
    for ev in push pull_request; do
      tname="${agent}--${owner}--${name}--${ev}"
      if trigger_exists "$tname"; then
        log "trigger exists: $tname"; SKIPPED[$agent]=$(( SKIPPED[$agent] + 1 )); continue
      fi
      out="$(run_capture guild --mode json trigger create --type webhook --integration github --event "$ev" \
              --agent "$full" --service-config "{\"repo\":\"$repo\"}" --name "$tname" "${WS_FLAG[@]}")"
      record_trigger "$tname" "$agent" "$repo" "$ev" "$out"
      CREATED[$agent]=$(( CREATED[$agent] + 1 ))
    done
  done < <(fleet_repos_of "$agent")
}

# ---------- main ----------
log "owner=$OWNER scanner=$SCANNER_INTEGRATION dry=$DRY build=$BUILD"
[[ "$DRY" == "1" ]] || mkdir -p "$BUILD"
load_existing_triggers

while IFS= read -r agent; do
  [[ -z "$agent" ]] && continue
  log "=== $agent ==="
  build_agent "$agent" "$TEMPLATE"
  make_webhook_triggers "$agent"
done < <(fleet_agents)

# warden: full-fleet cron
WARDEN_DONE="skipped (guild-agent/warden missing)"
if [[ -d "$WARDEN_SRC" || "$DRY" == "1" ]]; then
  log "=== $WARDEN_NAME ==="
  build_agent "$WARDEN_NAME" "$WARDEN_SRC" || true
  CRON="$(fleet_warden_cron)"
  tname="${WARDEN_NAME}--fleet--all--cron"
  CREATED[$WARDEN_NAME]=0; SKIPPED[$WARDEN_NAME]=0
  if trigger_exists "$tname"; then
    log "trigger exists: $tname"; SKIPPED[$WARDEN_NAME]=1
  else
    out="$(run_capture guild --mode json trigger create --type time --frequency CRON --cron-expression "$CRON" \
            --cron-timezone America/Los_Angeles --agent "$OWNER~$WARDEN_NAME" --name "$tname" \
            --input "$(fleet_repos_json)" "${WS_FLAG[@]}")"
    record_trigger "$tname" "$WARDEN_NAME" "*" "cron" "$out"
    CREATED[$WARDEN_NAME]=1
  fi
  WARDEN_DONE="cron '$CRON'"
fi

# ---------- summary ----------
echo
printf '%-20s %-6s %-9s %-9s %s\n' AGENT REPOS CREATED EXISTING REPOS_LIST
while IFS= read -r agent; do
  repos="$(fleet_repos_of "$agent" | paste -sd, -)"
  n="$(fleet_repos_of "$agent" | grep -c . || true)"
  printf '%-20s %-6s %-9s %-9s %s\n' "$agent" "$n" "${CREATED[$agent]:-0}" "${SKIPPED[$agent]:-0}" "$repos"
done < <(fleet_agents)
printf '%-20s %-6s %-9s %-9s %s\n' "$WARDEN_NAME" "$(fleet_all_repos | grep -c . || true)" "${CREATED[$WARDEN_NAME]:-0}" "${SKIPPED[$WARDEN_NAME]:-0}" "$WARDEN_DONE"
echo
log "expected webhook triggers after a real run: $(( $(fleet_all_repos | grep -c . || true) * 2 )) (+1 cron). Verify: guild trigger list --limit 200"
[[ "$DRY" == "1" ]] && log "DRY run: nothing executed. triggers.json untouched." || log "trigger ids appended to $TRIGGERS_OUT"
