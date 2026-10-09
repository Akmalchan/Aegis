# aegis-reporter

Hourly fleet report with charts, filed as a GitHub issue. Read-only on code.

Trigger: time, CRON `0 * * * *` (UTC), input `{"report_repo": "andriidrok1/aegis"}`.

Logic
1. `aegis_scanner_fleet_insights({})` (ClickHouse: rising_repos, noisy_rules, reopened, agent_latency).
2. Three charts as QuickChart image URLs (`https://quickchart.io/chart?w=600&h=300&c=<url-encoded Chart.js config>`,
   no image hosting): findings per repo (bar, now vs prev), scan latency per agent p50/p95 (bar), findings per rule (pie).
3. Markdown report: at-a-glance line, the three charts each with its table, reopened findings, notes.
4. Closes every open issue labeled `aegis-report` whose title starts with `AEGIS fleet report`, creates
   `AEGIS fleet report <ISO time>` with that label. Records `issue_closed` / `issue_opened` via `aegis_scanner_record_action`.

Tools: aegis_scanner_fleet_insights, aegis_scanner_record_action, github_issues_list_for_repo, github_issues_create, github_issues_update.
Placeholder in `agent.ts`: `__SCANNER_INTEGRATION__`.

```bash
guild agent init --name aegis-reporter --agent-type GUILD_TYPESCRIPT --template LLM --category development --directory build/_g/aegis-reporter --owner andriidrok1
sed 's/__SCANNER_INTEGRATION__/andriidrok1~aegis-scanner/g' guild-agent/reporter/agent.ts > build/_g/aegis-reporter/agent.ts
cd build/_g/aegis-reporter && npm install --save @guildai/agents-sdk@^0.7.8 @guildai-services/andriidrok1~aegis-scanner@^1.1.0 @guildai-services/guildai~github && npm run build
guild agent save --message "role agent" --wait --publish
guild workspace agent add andriidrok1~aegis-reporter --workspace andriidrok1~aegis
guild trigger create --workspace andriidrok1~aegis --type time --frequency CRON --cron-expression "0 * * * *" --cron-timezone UTC --agent andriidrok1~aegis-reporter --name aegis-reporter--fleet--hourly--cron --input '{"report_repo":"andriidrok1/aegis"}'
echo '{"report_repo":"andriidrok1/aegis"}' | guild agent test --mode json   # files a real report issue
```
Published 2026-10-09 as `andriidrok1~aegis-reporter`; trigger `aegis-reporter--fleet--hourly--cron`.
