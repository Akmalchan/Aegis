#!/usr/bin/env bash
# AEGIS credential policies: least privilege per agent on the guildai~github credential.
#
# For every agent in fleet.json:
#   ALLOW  the GitHub operations the sentinel needs, restricted to its own repos
#   DENY   every operation on every OTHER fleet repo (+ report repo), so a wrong-repo write is
#          visibly refused by Guild's credential proxy (the demo moment)
# For the warden: ALLOW read + issue ops on all fleet repos and issues_create on the report repo.
# Finally: delete the default unscoped allow-all rule, otherwise it keeps granting everything.
#
# Usage:
#   fleet/policies.sh                      # print rules + UI steps, write fleet/policies.json
#   APPLY=1 OWNER=x CRED_ID=<id> fleet/policies.sh   # apply through `guild credentials policy create`
#   DRY=1 APPLY=1 OWNER=x fleet/policies.sh          # print the apply commands only
#
# Variables: OWNER (Guild owner, needed for APPLY), CRED_ID (GitHub credential id; auto-detected from
# `guild credentials list --search github` when APPLY=1 and not DRY), WORKSPACE (optional scope),
# WARDEN_NAME (default aegis-warden), DRY, APPLY.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FLEET="$ROOT/fleet.json"
OUT="$ROOT/fleet/policies.json"
OWNER="${OWNER:-}"
CRED_ID="${CRED_ID:-}"
WORKSPACE="${WORKSPACE:-}"
WARDEN_NAME="${WARDEN_NAME:-aegis-warden}"
DRY="${DRY:-0}"
APPLY="${APPLY:-0}"

# Operation names = GitHub REST operationIds as exposed by guildai~github (tools are github_<op>).
SENTINEL_OPS="repos_get,repos_get_content,repos_get_branch,repos_create_commit_status,repos_list_pull_requests_associated_with_commit,repos_create_or_update_file_contents,git_create_ref,git_get_ref,issues_create,issues_update,issues_create_comment,issues_list_for_repo,issues_list_comments,pulls_create,pulls_create_review,pulls_list"
WARDEN_OPS="repos_get,repos_get_content,issues_list_for_repo,issues_create,issues_create_comment"

# ---- generate fleet/policies.json from fleet.json ----
python3 - "$FLEET" "$OUT" "$SENTINEL_OPS" "$WARDEN_OPS" "$WARDEN_NAME" "$WORKSPACE" <<'PY'
import json,sys
fleet_path,out,sops,wops,warden,ws=sys.argv[1:7]
d=json.load(open(fleet_path))
agents=d["agents"]; report=d.get("report_repo")
all_repos=[r for rs in agents.values() for r in rs]
sops=sops.split(","); wops=wops.split(",")
def rule(agent,decision,ops,repos,note):
    r={"credential":"guildai~github","decision":decision,"agents":[agent],
       "operations":ops,"resources":{"repositories":sorted(repos)},"note":note}
    if ws: r["workspaces"]=[ws]
    return r
rules=[]
for a,repos in agents.items():
    others=sorted((set(all_repos)-set(repos))|({report} if report else set()))
    rules.append(rule(a,"ALLOW",sops,repos,f"{a} may act only on its {len(repos)} repos"))
    rules.append(rule(a,"DENY",["*"],others,f"{a} explicitly denied on every other fleet repo (demo: visible refusal)"))
wrepos=sorted(set(all_repos)|({report} if report else set()))
rules.append(rule(warden,"ALLOW",wops,wrepos,"warden reads every fleet repo, writes drift report issue"))
doc={"generated_from":"fleet.json","credential":"guildai~github",
     "resource_shape_note":"BEST GUESS: --resources '{\"repositories\":[\"owner/name\",...]}'. Verify the key name in app.guild.ai > Credentials > GitHub > Policies > Add rule (the UI shows the exact JSON it sends). DENY beats ALLOW. '*' in operations means 'leave --operations empty = all operations'.",
     "delete_default_rule":"Yes. The default unscoped ALLOW-all rule must be deleted or the per-agent ALLOW rules are meaningless.",
     "rules":rules}
json.dump(doc,open(out,"w"),indent=2); open(out,"a").write("\n")
print(f"wrote {out} ({len(rules)} rules)")
PY

echo
echo "================ AEGIS credential policies (guildai~github) ================"
python3 - "$OUT" <<'PY'
import json,sys
doc=json.load(open(sys.argv[1]))
for r in doc["rules"]:
    ops=r["operations"]; ops_s="ALL operations" if ops==["*"] else f"{len(ops)} ops: "+", ".join(ops)
    print(f"\n[{r['decision']}] agent={r['agents'][0]}")
    print(f"   repos: {', '.join(r['resources']['repositories'])}")
    print(f"   {ops_s}")
    print(f"   # {r['note']}")
print("\n[DELETE] the default unscoped ALLOW-all rule on guildai~github (it overrides nothing but grants everything).")
PY

cat <<EOF

---------------- Apply in the UI (app.guild.ai) ----------------
1. app.guild.ai -> left nav "Credentials" -> row "GitHub" (guildai~github) -> tab "Policies".
2. For each [ALLOW] block above: "Add rule" -> Decision: ALLOW -> Scope: Agents = <agent>
   (and Workspace = ${WORKSPACE:-<your workspace>} if the picker exists) -> Operations: tick the listed operations
   -> Resources / Repositories: paste the repo list (owner/name, one per line or comma-separated) -> Save.
3. For each [DENY] block: "Add rule" -> Decision: DENY -> Agents = <agent> -> Operations: all (leave empty)
   -> Repositories: the "others" list -> Save. DENY overrides ALLOW, so never put the agent's own repos here.
4. Add the warden [ALLOW] rule the same way.
5. Delete the default rule (the one with no agent/workspace scope and no resource restriction).
6. Verify: from the sentinel-01 test session call github_issues_create on andriidrok1/aegis-target-04:
   the proxy must answer with a policy denial (that is the demo screenshot). Record it with
   aegis_scanner_record_action(kind="denied").

---------------- Apply from the CLI (guild credentials policy create, verified in v0.27.1) ----------------
EOF

# ---- optional: apply via CLI ----
cred="${CRED_ID:-<GITHUB_CRED_ID>}"
if [[ "$APPLY" == "1" && "$DRY" != "1" ]]; then
  [[ -n "$OWNER" ]] || { echo "OWNER is required with APPLY=1" >&2; exit 1; }
  if [[ -z "$CRED_ID" ]]; then
    CRED_ID="$(guild --mode json credentials list --search github --owner "$OWNER" 2>/dev/null | python3 -c '
import json,sys
d=json.load(sys.stdin); found=None
def walk(x):
    global found
    if found: return
    if isinstance(x,dict):
        name=" ".join(str(v) for v in x.values() if isinstance(v,str)).lower()
        if isinstance(x.get("id"),str) and "github" in name: found=x["id"]; return
        for v in x.values(): walk(v)
    elif isinstance(x,list):
        for v in x: walk(v)
walk(d); print(found or "")' || true)"
    [[ -n "$CRED_ID" ]] || { echo "could not detect GitHub credential id; set CRED_ID=<id> (guild credentials list --search github)" >&2; exit 1; }
    cred="$CRED_ID"
  fi
fi

ws_flag=""; [[ -n "$WORKSPACE" ]] && ws_flag="--workspaces $WORKSPACE "
CMDS="$(python3 - "$OUT" "$cred" "$OWNER" "$ws_flag" <<'PY'
import json,sys,shlex
doc,cred,owner,ws=json.load(open(sys.argv[1])),sys.argv[2],sys.argv[3],sys.argv[4]
for r in doc["rules"]:
    agent=f"{owner}~{r['agents'][0]}" if owner else r['agents'][0]
    ops="" if r["operations"]==["*"] else f"--operations {','.join(r['operations'])} "
    res=shlex.quote(json.dumps(r["resources"],separators=(",",":")))
    print(f"guild credentials policy create {cred} --decision {r['decision']} --agents {agent} {ops}{ws}--resources {res}")
print(f"guild credentials policy list {cred} --limit 100   # find the default rule id, then: guild credentials policy delete <policy-id>")
PY
)"
while IFS= read -r line; do
  echo "+ $line"
  if [[ "$APPLY" == "1" && "$DRY" != "1" && "$line" != *"policy list"* ]]; then eval "$line"; fi
done <<< "$CMDS"

echo
if [[ "$APPLY" == "1" && "$DRY" != "1" ]]; then
  echo "applied. Now delete the default rule: guild credentials policy list $cred, then guild credentials policy delete <id>."
else
  echo "(printed only. APPLY=1 OWNER=<owner> CRED_ID=<id> fleet/policies.sh runs them; the --resources JSON key is a best guess, check once in the UI.)"
fi
