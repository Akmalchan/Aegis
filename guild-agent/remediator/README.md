# aegis-remediator

Sub-agent of `aegis-sentinel-NN`. For one confirmed finding it files the `[AEGIS] ...` Issue (labels `aegis`,
`security`, body ends with `<!-- AEGIS-FP: <fingerprint> -->`) and, when the Semgrep rule shipped a `fix`, creates
branch `aegis/fix-<fingerprint>` from the push sha, commits the patched file (`AEGIS: fix <rule_id> in <path>`) and
opens a PR with body `Fixes #<issue>`. Every write is recorded with `aegis_scanner_record_action`.

Input: `{repo, sha, agent, finding, triage}` (triage = output of aegis-triage).
Output (JSON text): `{issue_number, pr_number|null, notes}`.

GitHub tools: `github_repos_get`, `github_repos_get_content`, `github_issues_create`, `github_git_create_ref`,
`github_repos_create_or_update_file_contents`, `github_pulls_create`. Scanner: `aegis_scanner_record_action`.

```bash
guild auth login
cd guild-agent/remediator && npm install && npx tsc --noEmit
guild agent init --name aegis-remediator --template LLM --agent-type GUILD_TYPESCRIPT --directory .
guild agent save --message "remediator v1" --wait --publish
guild agent test --mode json < ../../fleet/samples/remediator_input.json   # build from a triage output
```

The import `@guildai-services/__OWNER__~aegis-scanner` resolves only after stream B publishes the integration;
replace `__OWNER__` with the Guild owner (fleet/deploy.sh does the substitution). Offline typecheck: `npm run typecheck:offline`.
