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

## Round 3 (final flow): one push → fix_code → verify → one Issue → PR → merge → green (S2, 14:15–15:10 PDT)

Scenario fixed by the lead: per push the remediator takes ONE finding (ERROR before WARNING, `fix`/`fix_hint`
preferred), asks the scanner for the patch (`aegis_scanner_fix_code`, scanner **1.2.0**, published by S1 at 21:17),
commits it on `aegis/fix-<fp>`, proves it with `verify_fix`, writes one story Issue, and only when verified opens the
PR, labels `aegis:verified` and merges it (`github_pulls_merge`, method `merge`). Every other confirmed finding gets
an Issue only (`mode: "issue_only"`). The sentinel gates Issue-closing on a `scan_full` at HEAD (a diff scan does not
list findings the push left alone, so "not in CURRENT" was never proof that a finding is gone).

Published: remediator **1.0.6** `01a12284-2480-cf83-0000-8605e1a2180e` (21:15:19), sentinel
`01a12285-5f1e-cf83-0000-bf5fef9e3828` (21:16:36, "one fix per push, full-scan gate before closing, merge-aware"),
remediator **1.0.7** `01a1228b-7383-cf83-0000-8d6d95b76d61` (21:23:16, round-2 fixes below). The sentinel's
remediator tool calls the agent by name, so remediator republishes do not need a sentinel republish.

### Round 1 (21:16:49 push `c084f7c`, session `01a12286-aa15-5f3d-0000-92581f653e70`)

| time | what |
|---|---|
| 21:16:49 | push vulnerable `app.py` to main |
| 21:16:53 | session spawned (4 s) |
| 21:17:25 | status **failure** "4 new finding(s)" (36 s) |
| 21:17:39–54 | `aegis_triage` confirms `aegis.hardcoded-secret` |
| 21:18:14 | `aegis_scanner_fix_code` → `semgrep-rule-fix`, 1 s |
| 21:18:19 / 21:18:35 | branch `aegis/fix-c684c35ae1fe` + file commit `51a7ad2`; both webhook sessions (`01a12287-fee4`, `01a12288-3f4e`) → `verdict: ignored, notes: aegis fix branch` in 11 s each |
| 21:18:42–47 | `verify_fix` #1: targeted test FAILED at head (`KeyError: ADMIN_API_KEY` on import: the test imported `app` before setting the env var) |
| 21:19:01 | remediator re-edited and re-committed the file itself (`f2e44ba`), against the rules |
| 21:19:22–28 | `verify_fix` #2: static ✅ 4674 ms ("4 at base, 2 at head"), regression ⏭ (no suite), targeted ✅ 196 ms |
| 21:19:37 | Issue **#63** (story: Summary / Found / Validated / Fix diff / Verified per layer / Decision) |
| 21:19:49 | PR **#64**, 21:19:57 label `aegis:verified`, 21:20:03 status success on fix sha |
| 21:20:12 | **merged** by the agent (`github_pulls_merge`) → main `1ada435` |
| 21:20:15 | merge session `01a12289-bfc0…` → status **failure** "1 finding(s)" on the merge commit |
| 21:21:17 / 21:22:27 | Issues #65 (SQL concat), #66 (debug) in `issue_only` mode, no PR |

Push → PR 3 min 00 s, push → merge 3 min 23 s. Mechanics all work, content did not: the merged `app.py` was
syntax-broken. The LLM base64-encodes `new_content` for `create_or_update_file_contents` and the round trip mangled
three lines outside the span (`"%", + q`, `for"r in`, `if __main__ ==`). Verification missed it: Semgrep reports fewer
findings on a file it cannot fully parse (so "finding gone, nothing new" passed) and the targeted test only read the
source text, never imported the module. Second bug: the PR body said `Fixes #63`, so GitHub closed the Issue at merge
time and the verifier's "re-scanned" comment never happened (#63 shows CLOSED at 21:20:12 with no comment).

Round-2 fixes (remediator 1.0.7): commit through the Git Data API with plain text (`github_git_get_commit` →
`github_git_create_tree` with `content` → `github_git_create_commit` → `github_git_create_ref`), no base64 anywhere;
exactly one commit per run and no self-edits after a failed verify; the targeted test must import the app module
inside the test function (after `monkeypatch.setenv` for secrets) so a file that does not import fails L3; no GitHub
closing keywords in PR text. Reset at 21:23:43 (`2662bbd`, clean).

### Round 2 (21:24:52 push `cd10661`, session `01a1228e-0821-5f3d-0000-b118de0d5899`, remediator 1.0.8)

| time | what |
|---|---|
| 21:24:52 | push vulnerable `app.py` to main |
| 21:25:14 | status **failure** "4 new finding(s)" (22 s) |
| 21:26:01 | `fix_code`; 21:26:07–19 Git Data API commit `45e5a29` on `aegis/fix-*` (no base64, file parses) |
| 21:26:24 | `verify_fix` → verified |
| 21:26:40 | Issue **#68** (story), created **without labels** |
| 21:27:03 | PR **#69** (first `github_pulls_create` errored, retry ok), 21:27:10 label `aegis:verified`, 21:27:17 status success "fix verified: 2 layers passed" |
| 21:27:26 | **merged by the agent** → main `965bbe7` (push → merge 2 min 34 s) |
| 21:27:47 | merge-push session `01a12290-6017…` → status **success** "no new findings" (push → ✅ 2 min 55 s) |
| 21:28–21:30 | Issues #70 #71 #72 (`issue_only`, labelled) |

Gap: #68 stayed open. The merge-push session listed `labels=aegis` Issues, #68 was not among them (no label), so step 4.3
had no candidates and never ran `scan_full`/verifier. Fix (remediator **1.0.9** `01a12293-228a-cf83-0000-a48ae9428606`,
21:30:30): mandatory `github_issues_add_labels ["aegis","security"]` right after `github_issues_create`.

## Round 3 (final flow, auto-merge) (S3, 21:35–22:12 UTC)

Context: from ~21:36 to ~21:52 every Guild session failed ("invalid model ID" / "No LLM key"); the lead pinned the
workspace to OpenAI gpt-4o. Two of my rounds died in that window (21:36 push `68827eb`: remediator crashed with a
1963-char `tool_calls[0].id` right after `fix_code`; 21:50 push `552aa9b`: the sentinel ran the `remediation-playbook`
skill instead of the remediator and filed `severity:critical` Issues #73/#74 + base64 PR #75 by hand). gpt-4o also
returned `ignored: "no head commit message to scan"` on a normal push (21:54 `896e7bb`).

Prompt changes (sentinel only; remediator stays **1.0.9** `01a12293-228a-cf83-0000-a48ae9428606`):
- **1.0.10** `01a122a7-58da-cf83-0000-32ef5a84c9c8` (21:52): `skillsTools` removed from the sentinel and the "follow
  the skill rubric" paragraph replaced by: outside the INLINE FALLBACK never call `github_issues_create`,
  `github_pulls_create`, `github_git_create_ref`, `create_or_update_file_contents` yourself; Issue/branch/commit/PR/merge
  belong to `aegis_remediator`.
- **1.0.11** `01a122af-bf46-cf83-0000-4f8474daa309` (22:01): `ignored` only for aegis/ branches and deleted branches,
  a default-branch push is always scanned; ordering puts injection findings before hard-coded secrets inside a severity.
- **1.0.12** `01a122b7-f120-cf83-0000-5c5f35802911` (22:10, **not yet exercised by a run**): step 4.3 (scan_full +
  verifier) marked mandatory whenever OPEN is non-empty, with a self-check before leaving step 4.

### Run 3d on demo-target: merge proven (push `d1a65a3`, session `01a122b4-296d-5f3d-0000-2b2ad441adb3`, sentinel 1.0.11)

| time (UTC) | after push | what |
|---|---|---|
| 22:04:47 | 0 | push vulnerable `app.py` to main |
| 22:05:11 | 24 s | status **failure** "4 finding(s)" |
| 22:06:29 | 1 min 42 s | fix commit `751dced` on `aegis/fix-*` (Git Data API, fix_code patch) |
| 22:06:59 | 2 min 12 s | story Issue **#76** (labelled `aegis,security`) |
| 22:07:09 | 2 min 22 s | PR **#77** |
| 22:07:15 | 2 min 28 s | status success "fix verified: 2 layers passed" on the fix sha, label `aegis:verified` |
| 22:07:21 | 2 min 34 s | **PR #77 merged by the agent** (`github_pulls_merge`, merged_by `app/guild-ai-platform`) → main `cd39fcf` |
| 22:07:44 | 2 min 57 s | merge-push session `01a122b4-ea61-5f3d-0000-d72f582f0dd8` → status **success** "no new findings" |
| 22:08–22:10 | | Issues #78 (SQLi), #79 (debug), #80 (0.0.0.0) in `issue_only` mode, no PR |
| — | | **Issue #76 NOT closed.** The merge session listed the Issues, posted the ✅ commit comment and stopped: gpt-4o skipped 4.3 (no `scan_full`, no verifier). Round 2 failed the same step for a different reason (unlabelled Issue). 1.0.12 targets this; untested. |

Links: https://github.com/andriidrok1/aegis-demo-target/pull/77 ·
https://github.com/andriidrok1/aegis-demo-target/issues/76 ·
https://app.guild.ai/sessions/01a122b4-296d-5f3d-0000-2b2ad441adb3 ·
https://app.guild.ai/sessions/01a122b4-ea61-5f3d-0000-d72f582f0dd8

Demo-target is left as the run ended: main `cd39fcf` green, Issues #76 #78 #79 #80 open (reset block above).

## snipbox pre-run (andriidrok1/snipbox, push to `fork` only)

Two takes, both stopped at the verify gate, so **no PR and no merge on snipbox**:

| take | push | ❌ status | story Issue | decision |
|---|---|---|---|---|
| 1 (sentinel 1.0.10) | 21:54:29 `7d99545` | 21:54:57 (28 s) | #2 at 21:56:34 (2 min 05 s) | regression ❌, targeted ❌ at head → no PR |
| 2 (sentinel 1.0.11) | 22:04:49 `dfbee2e` | 22:05:09 (20 s) | #4 at 22:06:35 (1 min 46 s) | static ✅ 7.4 s, targeted ✅ (fails at base, passes at head), **regression ❌** "pytest exit 2: 1 error" → no PR |

Why: the agent picks `aegis.hardcoded-secret` in `snipbox/config.py` (gpt-4o ignored the "injection first" ordering),
the scanner's patch is `ADMIN_API_KEY = os.environ["ADMIN_API_KEY"]`, and snipbox's own test suite imports the config
without that env var, so collection errors out. The gate did its job (nothing reached main), but the merge beat cannot
come from snipbox with this finding. Options for a merge on snipbox: scanner `fix_code` emits
`os.environ.get("ADMIN_API_KEY", "")` for secrets, or `verify_fix` runs the suite with the env vars the patch reads, or
the remediator is forced to take the SQL injection (`search_snippets`) as the primary finding.

Sessions: take 1 https://app.guild.ai/sessions/01a122a9-38ea-5f3d-0000-ea94c3b77b61, take 2 https://app.guild.ai/sessions/01a122b2-9d61-5f3d-0000-9688ca47ab1c; trigger `01a12266-19dd-6639-0000-0ea59dc42e54`
(`guild trigger sessions 01a12266-19dd-6639-0000-0ea59dc42e54`).

State left: fork main `432cd7b` = `Revert "feat: snippet search endpoint"` (content = 0.4.2), all `aegis` Issues
closed, no `aegis/*` branches, drift-report Issues #1 #3 (label `aegis-report`) left open.

### Reset snipbox for a live take

```bash
R=andriidrok1/snipbox
cd ~/PycharmProjects/snipbox && git fetch fork && git checkout main && git reset --hard fork/main
gh pr list -R $R --state open --json number --jq '.[].number' | xargs -r -I{} gh pr close {} -R $R --delete-branch
gh issue list -R $R --state open --label aegis --json number --jq '.[].number' | xargs -r -I{} gh issue close {} -R $R -c "reset for live take"
gh api repos/$R/branches --jq '.[].name' | grep '^aegis/' | xargs -r -I{} gh api -X DELETE repos/$R/git/refs/heads/{}
# if main still has the vulnerable commit on top: normal revert, never force-push
git log --oneline -1 | grep -q "snippet search endpoint" && git revert --no-edit HEAD && git push fork main
# wait for the green status on that push, then the live take:
git am ~/PycharmProjects/aegis/demo/snipbox/vuln.patch && git push fork main
```
