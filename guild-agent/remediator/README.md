# aegis-remediator

Sub-agent of `aegis-sentinel-NN`. Since v1.0.6 (S2, 14:15 PDT) the sentinel calls it once per confirmed finding with a
`mode`:

- `mode: "fix"` (the ONE primary finding of a push: highest severity, `fix`/`fix_hint` preferred). Sequence:
  1. `aegis_scanner_fix_code({repo, sha, path, start_line, end_line, lines, rule_id, message, fix?, fix_hint?, cwe, agent})`
     (scanner 1.2.0, `POST /fix`) returns `{ok, replacement, new_content, diff, explanation, model, span, ms}`: the full
     patched file with only the flagged span changed. The agent never composes file contents itself.
  2. Commits `new_content` through the Git Data API as plain text (`github_git_get_commit` → `github_git_create_tree`
     with `content` on the entry → `github_git_create_commit` → `github_git_create_ref` `aegis/fix-<fp>`) → `FIX_SHA`.
     Exactly one commit per run; no base64 (round 1 proved the LLM mangles a 1.3 KB file when it base64-encodes it).
  3. Writes a small benign pytest regression test that imports the app module inside the test (must FAIL at the
     vulnerable sha, PASS at `FIX_SHA`; a file that does not import fails the layer) and calls
     `aegis_scanner_verify_fix({repo, base_sha: push sha, head_sha: FIX_SHA, fingerprint, rule_id, path, agent,
     test_code, test_path})` → L1 static re-scan, L2 repo tests, L3 targeted test, each with ms.
  4. ONE Issue with the whole story: Summary / 1. Found / 2. Validated (triage verdict + targeted test) / 3. Fix
     (diff from fix_code, model, ms) / 4. Verified (one line per layer, ms) / 5. Decision. Labels `aegis`, `security`,
     footer, `<!-- AEGIS-FP: <fp> -->` last line.
  5. Only when `verified` is true: PR (body references the Issue without closing keywords, so the sentinel's
     re-scan of the merge commit is what closes it; includes the layer summary), label `aegis:verified`, `set_status
     success` on `FIX_SHA`, then `github_pulls_merge({merge_method: "merge"})`. If the merge fails the PR stays open
     and the Issue gets a comment saying so. Not verified → no PR, nothing pushed to the base branch, Issue says
     "Could not verify: <layer> failed". `fix_code` `ok: false` → Issue only.
- `mode: "issue_only"` (every other confirmed finding of the same push): the Issue with a "no PR: one fix per push"
  decision line and nothing else.

Every write is recorded with `aegis_scanner_record_action` (`issue_opened`, `pr_opened`, `verified` / `verify_failed`;
the `kind` enum has no `pr_merged`, so the merge is reported in the Issue comment and the output JSON only).

Input: `{repo, sha, agent, finding, triage, branch?, mode?}` (`mode` defaults to `fix`). Output (JSON text):
`{issue_number, pr_number|null, fix_sha|null, merge_sha|null, verified: bool|null, merged: bool, layers: [{name, passed, ms}], notes}`.

GitHub tools: `github_repos_get`, `github_repos_get_content`, `github_issues_create`, `github_issues_create_comment`,
`github_issues_add_labels`, `github_git_get_commit`, `github_git_create_tree`, `github_git_create_commit`,
`github_git_create_ref`, `github_git_update_ref`, `github_pulls_create`, `github_pulls_merge` (`{owner, repo, pull_number, merge_method, commit_title?}`). Scanner (1.2.0):
`aegis_scanner_record_action`, `aegis_scanner_fix_code`, `aegis_scanner_verify_fix`, `aegis_scanner_set_status`.
Published: v1.0.7 `01a1228b-7383-cf83-0000-8d6d95b76d61` (14:23 PDT, "S2 r2: Git Data API commit, test imports module, no closing keywords"); v1.0.6 `01a12284-2480-cf83-0000-8605e1a2180e` (14:15 PDT, "S2: fix_code + verify + one-Issue story + merge");
v1.0.5 `01a12252-8649-…` (13:21, "R4: fix_hint + branch input"); v1.0.4 `01a1224a-b979-…` (13:12, "W: verify loop").
Round results on `andriidrok1/aegis-demo-target`: see `docs/LIVE-RUN.md`, "Round 3 (final flow)".

```bash
guild auth login
cd guild-agent/remediator && npm install && npx tsc --noEmit
guild agent init --name aegis-remediator --template LLM --agent-type GUILD_TYPESCRIPT --directory .
guild agent save --message "remediator v1" --wait --publish
guild agent test --mode json < ../../fleet/samples/remediator_input.json   # build from a triage output
```

The import `@guildai-services/__OWNER__~aegis-scanner` resolves only after stream B publishes the integration;
replace `__OWNER__` with the Guild owner (fleet/deploy.sh does the substitution). Offline typecheck: `npm run typecheck:offline`.
