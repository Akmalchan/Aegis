#!/usr/bin/env bash
# AEGIS credential policies: least privilege per agent on the guildai~github credential.
#   ALLOW  each sentinel the GitHub operations it needs, only on its own repos
#   DENY   each sentinel everything on every OTHER fleet repo (+ report repo): a wrong-repo write is
#          refused by Guild's credential proxy (the demo moment)
#   ALLOW  the warden read + issue ops on all fleet repos
#
#   fleet/policies.sh                                  # write fleet/policies.json, print copy-pasteable commands
#   APPLY=1 OWNER=andriidrok1 fleet/policies.sh        # apply them (CRED_ID auto-detected from `guild credentials list`)
# Vars: OWNER, CRED_ID, WORKSPACE (default aegis), WARDEN_NAME (aegis-warden), APPLY (default 0), DRY.
#
# Verified (guild 0.27.1): `guild credentials policy create <cred-id> --decision ALLOW|DENY --operations a,b
#   --workspaces w --agents owner~name --resources '<json>'`. The resource keys come from
#   `guild --mode json integration get guildai~github` -> protocol_config.policy_resources:
#   "repos" (owner/repo, fnmatch patterns ok) and "methods" (HTTP verbs). Operation names are NOT documented
#   by --help; we use the OpenAPI operationIds (issues_create). If the proxy refuses them, retry with the
#   tool-name form (github_issues_create). Nothing has been applied yet.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FLEET="$ROOT/fleet.json"; OUT="$ROOT/fleet/policies.json"
OWNER="${OWNER:-}"; CRED_ID="${CRED_ID:-}"; WORKSPACE="${WORKSPACE:-aegis}"
WARDEN_NAME="${WARDEN_NAME:-aegis-warden}"; DRY="${DRY:-0}"; APPLY="${APPLY:-0}"
[[ "$WORKSPACE" == *~* ]] || WORKSPACE="${OWNER:-<owner>}~$WORKSPACE"

WARDEN_OPS="repos_get,repos_get_content,issues_list_for_repo,issues_create,issues_create_comment"

python3 - "$FLEET" "$OUT" "$SENTINEL_OPS" "$WARDEN_OPS" "$WARDEN_NAME" "$WORKSPACE" <<'PY'
import json,sys
fleet,out,sops,wops,warden,ws=sys.argv[1:7]
d=json.load(open(fleet)); agents=d["agents"]; report=d.get("report_repo")
all_repos=[r for rs in agents.values() for r in rs]
def rule(agent,decision,ops,repos,note):
    return {"decision":decision,"agents":[agent],"workspaces":[ws],"operations":ops.split(",") if ops else [],
            "resources":{"repos":sorted(repos)},"note":note}
rules=[]
for a,repos in agents.items():
    others=sorted((set(all_repos)-set(repos))|({report} if report else set()))
    rules.append(rule(a,"ALLOW",sops,repos,f"{a} may act only on its {len(repos)} repos"))
    rules.append(rule(a,"DENY","",others,f"{a}: all operations denied on every other fleet repo"))
rules.append(rule(warden,"ALLOW",wops,sorted(set(all_repos)|({report} if report else set())),"warden reads every fleet repo, writes the drift report issue"))
doc={"generated_from":"fleet.json","credential":"guildai~github",
     "resources_key":"repos (verified: integration get guildai~github -> protocol_config.policy_resources; fnmatch ok). Empty operations = all.",
     "operations_note":"OpenAPI operationIds (issues_create). Unverified by --help; fallback form is github_issues_create.",
     "delete_default_rule":"After applying, delete the default unscoped ALLOW-all policy or the per-agent rules change nothing.",
     "rules":rules}
json.dump(doc,open(out,"w"),indent=2); open(out,"a").write("\n")
print(f"wrote {out} ({len(rules)} rules)")
PY

if [[ "$APPLY" == "1" && "$DRY" != "1" && -z "$CRED_ID" ]]; then
  CRED_ID="$(guild --mode json credentials list --owner "${OWNER:?OWNER required with APPLY=1}" --search github --limit 50 2>&1 | python3 -c '
import json,sys
for c in json.load(sys.stdin).get("items",[]):
    if c.get("integration",{}).get("full_name")=="guildai~github": print(c["id"]); break' || true)"
  [[ -n "$CRED_ID" ]] || { echo "no guildai~github credential: connect GitHub in app.guild.ai > Credentials, or pass CRED_ID=<id>" >&2; exit 1; }
fi
cred="${CRED_ID:-<GITHUB_CRED_ID>}"

echo; echo "# guildai~github policies (credential $cred). Copy-paste, or APPLY=1 OWNER=<owner> fleet/policies.sh"
python3 - "$OUT" "$cred" "$OWNER" <<'PY' | while IFS= read -r line; do
import json,sys,shlex
doc,cred,owner=json.load(open(sys.argv[1])),sys.argv[2],sys.argv[3]
for r in doc["rules"]:
    agent=f"{owner}~{r['agents'][0]}" if owner else f"<owner>~{r['agents'][0]}"
    ops=f" --operations {','.join(r['operations'])}" if r["operations"] else ""
    print(f"guild credentials policy create {cred} --decision {r['decision']} --agents {agent} --workspaces {r['workspaces'][0]}{ops} --resources {shlex.quote(json.dumps(r['resources'],separators=(',',':')))}   # {r['note']}")
print(f"guild credentials policy list {cred} --limit 100   # then delete the default unscoped ALLOW-all rule in app.guild.ai > Credentials > GitHub > Policies")
PY
  echo "$line"
  if [[ "$APPLY" == "1" && "$DRY" != "1" && "$line" != *"policy list"* ]]; then eval "${line%%   #*}"; fi
done
[[ "$APPLY" == "1" && "$DRY" != "1" ]] && echo "applied. Now delete the default rule (see last line)." || echo "(printed only; nothing applied)"
