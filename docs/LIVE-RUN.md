# Live run on andriidrok1/aegis-demo-target (2026-10-09, 13:34–14:05 PDT)

Rehearsal of the full loop through real GitHub webhooks → Guild triggers → `aegis-sentinel-01`. All times UTC
(PDT = UTC−7). Workspace `andriidrok1~aegis`, GitHub credential `01a1225e-d129-c369-0000-57053b62535b`.

## Triggers (created by `ONLY_TRIGGERS=1 ONLY_AGENT=aegis-sentinel-01 ONLY_REPO=andriidrok1/aegis-demo-target OWNER=andriidrok1 fleet/deploy.sh`, no script changes needed)

| name | id | event |
|---|---|---|
| `aegis-sentinel-01--andriidrok1--aegis-demo-target--push` | `01a1225f-c54b-6639-0000-49b79296e094` | push |
| `aegis-sentinel-01--andriidrok1--aegis-demo-target--pull_request` | `01a1225f-ca6d-6639-0000-9c95fcbc063f` | pull_request |

Recorded in `fleet/triggers.json`. Both bound to the workspace agent with `should_autoupdate: true`, so they run the
latest published sentinel version (1.0.7 during (a)/(b), 1.0.8 from 20:49:00).

## (a) Harmless push → green

| time | what |
|---|---|
| 20:34:47 | `git push` README-only commit `08ab4c7` to main |
| 20:34:48 | Guild session `01a12260-25be-5f3d-0000-c14244147afd` spawned (1 s) |
| 20:35:22 | commit status `AEGIS / security-check` = **success** "no new findings" |
| 20:35:34 | agent final: `{"verdict":"safe", ...}` |

Push → green status: 35 s. Push → session done: 47 s.

## (b) Vulnerable push → red, Issues, fix PR, verified

Vulnerable `app.py` (hard-coded key, string-built SQL, `debug=True` on `0.0.0.0`) cherry-picked from
`verify-test/vuln` onto main as `02f24a5` (linear history, no force push).

| time | what |
|---|---|
| 20:35:58 | `git push` `02f24a5` |
| 20:35:59 | session `01a12261-3a5b-5f3d-0000-b7d83ce5782d` spawned |
| ≤20:36:24 | commit status = **failure** (first poll, ≤26 s after push) |
| 20:36:52 | `aegis_triage` confirms `aegis.hardcoded-secret` (critical, CWE-798, confidence 1.0) |
| 20:37:12 | Issue **#1** opened (labels `aegis`, `security`, `<!-- AEGIS-FP -->` marker) |
| 20:37:34 | PR **#2** opened from `aegis/fix-c684c35ae1fe` |
| 20:37:50 | PR #2 labelled **`aegis:verified`** (verify_fix: static pass 3.8 s, targeted test pass) |
| 20:38:59 | Issue #6 `aegis.sql-string-concat` (no `fix` in finding → Issue only, no PR) |
| 20:39:52 | Issue #9 `aegis.flask-debug-true` → PR #12, verify **failed** (fix introduced `avoid_app_run_with_bad_host`), "could not verify" path |
| 20:42:32 | Issue #19 `avoid_app_run_with_bad_host` (Issue only) |
| 20:43:19 | agent final: `{"verdict":"unsafe","issues_opened":[1,6,9,19],"prs_opened":[2,12],"prs_verified":[2]}` |

Push → Issue: 74 s. Push → fix PR: 96 s. Push → verified label: 112 s. Whole session: 7 min 20 s (4 findings, sequential sub-agents).

Links: https://github.com/andriidrok1/aegis-demo-target/issues/1 · https://github.com/andriidrok1/aegis-demo-target/pull/2 ·
session https://app.guild.ai/sessions/01a12261-3a5b-5f3d-0000-b7d83ce5782d (open from the workspace if the path differs).

**What went wrong: the self-trigger cascade.** Every `aegis/fix-*` branch the remediator pushes and every PR it opens
fires the same push / pull_request triggers. Each spawned a fresh sentinel (v1.0.7) that scanned the fix branch,
re-filed the same findings and opened more fix branches: 29 extra sessions, 47 Issues and 13 PRs from one push
(duplicates #3–#5, #10–#18, #20–#60, PRs #7 #8 #16 #23 #25 #29 #35 #47 #52 #54 #56). The verifier in those
sessions also closed Issues legitimately ("✅ Re-scanned app.py at a6c752e: aegis.hardcoded-secret no longer
present. Closing."), so the closing path is proven, just on the wrong trigger.

**Fix (published 20:49:00 as sentinel 1.0.8, version id `01a12268-c0a5-cf83-0000-3ce815e080c0`):** step 1.0 in the
prompt (`guild-agent/sentinel/agent.ts` + scratch copy): if `ref` starts with `refs/heads/aegis/` or the PR head ref
starts with `aegis/`, output `{"verdict":"ignored","notes":"aegis fix branch"}` and stop. Confirmed on session
`01a12270-dc87…` at 20:49:56 (PR event on `aegis/fix-18530b50f025` → ignored). No new sessions after 20:49:47.

**Also observed (not fixed, remediator-side):** the remediator rewrites the whole file instead of swapping the flagged
lines. PR #2 also changed `port=5000→5080` and dropped the `row = cur.fetchone()` line (main was briefly broken
Python after the merge); PR #12 mangled the `/users/<int:user_id>` route. The scanner finding for
`aegis.sql-string-concat` carries only `fix_hint`, so no PR is opened for the SQL injection: Issue only.

## (c) Close the loop

1. `gh pr merge 2 --merge --delete-branch` at 20:49:25 → main `6f2a01a`. Session `01a1226d-8ee8-5f3d-0000-47fd207dd4e5`
   (20:49:27 → 20:53:53): verdict **unsafe** (PR #2 touched the `app.run` line, so `debug=True` showed up as a new
   finding in the diff), status **failure** "2 finding(s)", Issue #41 + PR #47, and the verifier closed #34 #38
   (hard-coded key gone). Lesson for the demo: merge only a PR whose diff is exactly the flagged lines, or push the
   prepared fix.
2. Clean `app.py` (from `verify-test/fix`) pushed to main as `df22de9` at 20:55:38. Session
   `01a12273-4545-5f3d-0000-aa3dd850211f` (20:55:42 → 21:03:00): status **success** at 21:00:59, agent final
   `{"verdict":"safe","issues_closed":[40,41,42,43,44,45,49,50,53,55,57,60]}`, each with a "re-scanned at df22de9"
   comment. Push → green: 5 min 21 s, push → done: 7 min 18 s, because 40+ open Issues from the cascade had to be
   re-checked one GitHub round-trip at a time. On a repo with 1–4 open Issues this is the 60–90 s path seen in (a)/(b).

Repo state after the reset below (21:04): main = `df22de9` (clean app.py), 0 open Issues, 0 open PRs, branches
`main`, `verify-test/fix`, `verify-test/vuln`. Ready for the next rehearsal: one vulnerable push (step 4 in the reset block).

## Policies

`APPLY=1 CRED_ID=01a1225e-d129-c369-0000-57053b62535b OWNER=andriidrok1 fleet/policies.sh` ran clean: the CLI
accepted `--operations` and `--resources '{"repos":[...]}'` for all 7 rules (20:49:38–20:49:41):

| policy id | decision | agent | scope |
|---|---|---|---|
| `01a1226d-ba42-02e7-0000-9ea318fa093a` | ALLOW | sentinel-01 | 15 ops on `andriidrok1/{snipbox,aegis-demo-target,aegis-target-01}` |
| `01a1226d-bbed-02e7-0000-5a1716fefe01` | DENY | sentinel-01 | all ops on the other 6 fleet repos + `Akmalchan/Aegis` |
| `01a1226d-bdcb…` / `01a1226d-c018…` | ALLOW / DENY | sentinel-02 | same pattern |
| `01a1226d-c1df…` / `01a1226d-c366…` | ALLOW / DENY | sentinel-03 | same pattern |
| `01a1226d-c4df-02e7-0000-fc3fa57f2a62` | ALLOW | warden | 5 read/issue ops on all 10 repos |

The default unscoped ALLOW-all policy `01a1225e-d131-02e7-0000-c3da7083897e` (created with the credential at
20:33:21) is still there. DENY wins over it (see below), but delete it before the demo if the "sentinel-01 can
only see 3 repos" screen must show no ALLOW-all row: `guild credentials policy delete 01a1225e-d131-02e7-0000-c3da7083897e`.
Note `fleet.json` was switched from `vincivv/*` to `andriidrok1/*` owners before this run (uncommitted), so the
policies name `andriidrok1/aegis-target-0N`.

**Deny proof.** `guild agent test --mode json --workspace andriidrok1~aegis --agent-version 01a12268-c0a5-… <
push_foreign.json` with the push payload rewritten to `andriidrok1/aegis-target-04` (in sentinel-01's DENY list),
session `01a12270-29cd-f268-0000-a4036ffc41e1` (20:52:50):

- `aegis_scanner_scan_diff` 200 → safe
- `aegis_scanner_set_status` 200 (our own integration; the GitHub policy does not fence it, see gap below)
- `github_issues_list_for_repo` → task **ERROR**, `http_status_code: null` (refused by the credential proxy before GitHub)
- `github_repos_list_pull_requests_associated_with_commit` → **ERROR**
- `github_repos_create_commit_comment` → **ERROR**
- agent final notes: "GitHub API access is forbidden; could not list issues or create commit comment."

The CLI stream itself died with "The connection was aborted before receiving a response" after ~90 s; read the
session with `guild --mode json session tasks <id>` / `session events <id>` instead. Gap worth saying out loud:
`set_status` goes through the scanner integration, so a fenced agent can still paint a commit status on a foreign
repo; the scanner should check its own agent→repo map (`fleet.json`) before calling GitHub.

## Reset demo-target for the next rehearsal

```bash
R=andriidrok1/aegis-demo-target
# 1. close every open PR and Issue the agents opened (closing fires pull_request:closed → sentinel ignores it)
gh pr list -R $R --state open --json number --jq '.[].number' | xargs -I{} gh pr close {} -R $R --delete-branch
gh issue list -R $R --state open --limit 200 --json number --jq '.[].number' | xargs -I{} gh issue close {} -R $R -c "reset for rehearsal"
# 2. delete leftover fix branches (deleted-branch pushes are ignored by the sentinel)
gh api repos/$R/branches --jq '.[].name' | grep '^aegis/' | xargs -I{} gh api -X DELETE repos/$R/git/refs/heads/{}
# 3. put the clean app.py on main (verify-test/fix = known-clean, scanner reports 0 findings)
cd /home/andrii/PycharmProjects/aegis-demo-target && git fetch origin && git checkout main && git reset --hard origin/main
git checkout origin/verify-test/fix -- app.py && git commit -m "reset: clean app.py" && git push origin main
# 4. (optional) re-enable the vulnerable push in one command for the demo:
#    git checkout origin/verify-test/vuln -- app.py && git commit -m "feat: add /search" && git push origin main
```

Then wait for the reset push's session to finish (`guild trigger sessions 01a1225f-c54b-6639-0000-49b79296e094`) before
the next push, so the verifier is not racing a new scan.
