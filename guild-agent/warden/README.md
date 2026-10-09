# aegis-warden

Fleet supervisor. Runs on a Guild time trigger (cron) with a fixed input and:

1. `github_repos_get` + `github_repos_get_branch` -> default-branch sha, then `aegis_scanner_scan_full` for every repo.
2. `aegis_scanner_fleet_insights({hours: 24})` (ClickHouse: rising_repos, noisy_rules, reopened, agent_latency).
3. Closes the previous open `AEGIS drift report ...` issue (label `aegis-report`) in `report_repo` and files a new one
   with per-repo table + the four insight tables + new ERROR findings.
4. If any ERROR finding is new (`seen_before` 0/absent): one email via `emailTools` (goes to the Guild account owner's
   address; confirm the Email credential in app.guild.ai -> Credentials -> Email) with a 5-line summary.
5. Records `issue_closed` / `issue_opened` / `email` through `aegis_scanner_record_action` as agent `aegis-warden`.

Input: `{"repos": ["owner/name", ...], "report_repo": "Akmalchan/Aegis"}`. Output: one line + JSON
`{report_issue, repos_scanned, findings, new_error, emailed, errors[]}`.

```bash
guild auth login
cd guild-agent/warden && npm install && npx tsc --noEmit
guild agent init --name aegis-warden --template LLM --agent-type GUILD_TYPESCRIPT --directory .
guild agent save --message "warden v1" --wait --publish
echo '{"repos":["vincivv/snipbox"],"report_repo":"Akmalchan/Aegis"}' | guild agent test --mode json
guild trigger create --type time --frequency CRON --cron-expression "*/30 * * * *" \
  --agent <owner>~aegis-warden --name aegis-warden-cron \
  --input '{"repos":["vincivv/snipbox"],"report_repo":"Akmalchan/Aegis"}'
```

The exact email tool name inside `emailTools` is not documented; the agent spreads the whole `emailTools` object and the
prompt says "use the email tool". Check it with `guild agent capabilities <owner>~aegis-warden` after publishing.
Offline typecheck: `npm run typecheck:offline`.
