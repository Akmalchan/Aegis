# Evals

`sentinel.json` is a Guild evaluation spec (docs.guild.ai/platform/evaluations): 2 samples × 1 epoch = 2 trials,
checks `hasToolCall`, `toolCallContains`, `llmJudge` (field names validated against docs.guild.ai/platform/evaluations and
/reference/evals on 2026-10-09; a fourth type `outputMatches` {mode, value|referencePath, caseSensitive} exists too).
The commit mark is `github_repos_create_commit_comment`: the GitHub integration has no commit-status operation, so the old
`github_repos_create_commit_status` checks could never pass. The `hasToolCall github_issues_create` requirement for the
vulnerable push lives inside the llmJudge rubric because checks apply to every sample and the clean push must NOT
create an issue.

Run it in the UI (evals are gated per account; ask Guild support at the booth if the menu is missing):

1. app.guild.ai -> **Agents** -> **Setup** -> **Evals**.
2. New evaluation -> agent `<owner>~aegis-sentinel-01` -> paste `evals/sentinel.json` (or upload the file).
3. Before running: replace the two `after` shas with real commits the scanner can fetch
   (`git -C ../snipbox rev-parse demo/clean demo/vuln`) and make sure the `aegis-scanner` integration is published
   and the tunnel is up (stream B), otherwise `aegis_scanner_scan_diff` fails and every check fails with it.
4. Run; each trial has a 600 s timeout. Expect: scan_diff + commit comment on both, issues_create only on push_vuln.
   The docs mention evaluator "emulator responses": tool calls may be emulated rather than hitting GitHub/the scanner, in
   which case the sha caveat in step 3 does not apply. Top-level `name`/`description` and per-sample `name` are not in the
   documented spec (only `samples`, `checks`, `epochs`); drop them if the validator rejects unknown keys.

Limits: 100 samples, 3 epochs, 300 trials. Tool names in the checks must match `guild agent capabilities`.
