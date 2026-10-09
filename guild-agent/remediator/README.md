# aegis-remediator

Sub-agent of `aegis-sentinel-NN`. For one confirmed finding it files the `[AEGIS] ...` Issue (labels `aegis`,
`security`, body ends with `<!-- AEGIS-FP: <fingerprint> -->`) and, when the Semgrep rule shipped a `fix`, creates
branch `aegis/fix-<fingerprint>` from the push sha, commits the patched file (`AEGIS: fix <rule_id> in <path>`) and
opens a PR with body `Fixes #<issue>`. Then it PROVES the fix (STEP 3, added 13:12): writes a small benign pytest
regression test that must fail on the vulnerable sha and pass on the fix, calls `aegis_scanner_verify_fix({repo,
base_sha: push sha, head_sha: fix-branch head, fingerprint, rule_id, path, agent, test_code, test_path})` and, when
`verified` is true, labels the PR `aegis:verified`, comments the three layers (static / regression / targeted test,
ms each) and sets commit status `success` "fix verified: N layers" on the fix sha; otherwise comments "could not
verify: layer X failed" and leaves the PR as is. Every write is recorded with `aegis_scanner_record_action`
(`issue_opened`, `pr_opened`, `verified` / `verify_failed`).

Input: `{repo, sha, agent, finding, triage, branch?}` (triage = output of aegis-triage; `branch` = pushed branch, used as PR
base, default = repo default branch; `finding` may carry `fix_hint`, which goes into the Issue text, never into a patch).
Output (JSON text): `{issue_number, pr_number|null, fix_sha|null, verified: bool|null, layers: [{name, passed, ms}], notes}`.

GitHub tools: `github_repos_get`, `github_repos_get_content`, `github_issues_create`, `github_issues_create_comment`,
`github_issues_add_labels`, `github_git_create_ref`, `github_repos_create_or_update_file_contents`,
`github_pulls_create`. Scanner (1.1.0): `aegis_scanner_record_action`, `aegis_scanner_verify_fix`, `aegis_scanner_set_status`.
Published: v1.0.5 `01a12252-8649-cf83-0000-e0b3fe8e1b97` (13:21, "R4: fix_hint + branch input"); v1.0.4 `01a1224a-b979-…` (13:12, "W: verify loop").
R4 real run 13:18: called by the sentinel with a valid input (no schema error), `issues_create` failed Unauthorized
(GitHub credential not connected), returned `issue_number: null`; output must be JSON only even then (fixed in 1.0.5).

```bash
guild auth login
cd guild-agent/remediator && npm install && npx tsc --noEmit
guild agent init --name aegis-remediator --template LLM --agent-type GUILD_TYPESCRIPT --directory .
guild agent save --message "remediator v1" --wait --publish
guild agent test --mode json < ../../fleet/samples/remediator_input.json   # build from a triage output
```

The import `@guildai-services/__OWNER__~aegis-scanner` resolves only after stream B publishes the integration;
replace `__OWNER__` with the Guild owner (fleet/deploy.sh does the substitution). Offline typecheck: `npm run typecheck:offline`.
