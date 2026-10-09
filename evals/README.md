# Evals

`sentinel.json` is a Guild evaluation spec (docs.guild.ai/platform/evaluations): 2 samples × 1 epoch = 2 trials,
checks `hasToolCall`, `toolCallContains`, `llmJudge`. The `hasToolCall github_issues_create` requirement for the
vulnerable push lives inside the llmJudge rubric because checks apply to every sample and the clean push must NOT
create an issue.

Run it in the UI (evals are gated per account; ask Guild support at the booth if the menu is missing):

1. app.guild.ai -> **Agents** -> **Setup** -> **Evals**.
2. New evaluation -> agent `<owner>~aegis-sentinel-01` -> paste `evals/sentinel.json` (or upload the file).
3. Before running: replace the two `after` shas with real commits the scanner can fetch
   (`git -C ../snipbox rev-parse demo/clean demo/vuln`) and make sure the `aegis-scanner` integration is published
   and the tunnel is up (stream B), otherwise `aegis_scanner_scan_diff` fails and every check fails with it.
4. Run; each trial has a 600 s timeout. Expect: scan_diff + commit_status on both, issues_create only on push_vuln.

Limits: 100 samples, 3 epochs, 300 trials. Tool names in the checks must match `guild agent capabilities`.
