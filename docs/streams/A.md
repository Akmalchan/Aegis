# Stream A — Guild agents + fleet (owner: Andrii)

Paste this whole file as the first message of a Claude Code session opened in this repo.

## Context for Claude

Read `PLAN.md`, `openapi.yaml`, `fleet.json`, `guild-agent/agent.ts` (v1 analyst, to be replaced) first. Guild facts you must respect:
- Agents are TypeScript `llmAgent` from `@guildai/agents-sdk`; `inputSchema` must be a root `z.object`; zod `~4.3.0`.
- No shell, no `fetch`/`axios` inside agents. External calls go through integrations. Our scanner is a custom integration named `aegis-scanner` (see `openapi.yaml`); its tools will be importable as `import { aegisScannerTools } from "@guildai-services/<owner>~aegis-scanner"` with tool names `aegis_scanner_scan_diff`, `aegis_scanner_scan_full`, `aegis_scanner_record_action`, `aegis_scanner_fleet_insights`. Until stream B publishes it, write the code against those names.
- GitHub tools: `import { gitHubTools } from "@guildai-services/guildai~github"`, names = `github_<operationId>` from GitHub's REST spec (`github_issues_create`, `github_issues_update`, `github_issues_create_comment`, `github_issues_list_for_repo`, `github_repos_get_content`, `github_repos_create_commit_status`, `github_repos_list_pull_requests_associated_with_commit`, `github_pulls_create_review`, `github_pulls_create`, `github_git_create_ref`, `github_repos_create_or_update_file_contents`, `github_repos_get_branch`). Verify with `guild agent capabilities` after `npm install`; use `pick()` to keep tool lists short.
- Webhook triggers pass the **raw GitHub payload** as agent input. Push: `repository.full_name`, `before`, `after`, `commits[].added/modified`. Pull request: `action`, `pull_request.head.sha`, `pull_request.base.sha`, `number`.
- Skills: `skillsTools` in tools; `guild skill create <name> --overview ...` then `guild skill version create <owner>~<name> --version-number 1.0.0 --description "Use when ..." --body-file file.md`.
- Sub-agents: declared in `guild.yaml` under `sub_agents: [{name, version}]`; they appear as tools of toolType "agent". If calling them does not work within 40 minutes, fold their logic into the sentinel prompt and move on.
- Triggers from CLI: `guild trigger create --type webhook --integration github --event push --agent <owner>~aegis-sentinel-01 --service-config '{"repo":"owner/name"}'`; cron: `--type time --frequency CRON --cron-expression "*/30 * * * *" --input '{...}'`.
- Credential policies: ALLOW/DENY rules scoped to agents + repos, set in app.guild.ai (Credentials → GitHub → Policies). Write the rules as JSON in `fleet/policies.sh` plus the exact UI steps if no CLI exists.
- Workspace variables: `{{env.NAME}}` in prompts, `task.env.NAME` in code. Workspace context: `guild workspace context edit`.

## Spawn 3 subagents IN PARALLEL (Agent tool, one message), then integrate

### A1 — sentinel (owns `guild-agent/sentinel/`, `fleet/samples/`)
Build `guild-agent/sentinel/{agent.ts,package.json,tsconfig.json,guild.yaml,README.md}`:
- `llmAgent`, `inputSchema: z.object({}).passthrough()` (raw push or pull_request payload), `mode: "one-shot"`.
- tools: `pick(aegisScannerTools, [scan_diff, record_action])` + GitHub picks above + `skillsTools`.
- System prompt (precise, numbered): parse payload → call `aegis_scanner_scan_diff(repo, before, after, agent="{{env.AGENT_NAME}}" or from guild.yaml)` → list open issues with label `aegis` and read `<!-- AEGIS-FP: x -->` markers → **safe**: `github_repos_create_commit_status(state=success, context="AEGIS / security-check")`, if a PR is associated → approve review; close every open AEGIS issue whose fingerprint is no longer in findings (comment "re-scanned at <sha>") → **unsafe**: commit status `failure`, for each finding not already open and not `dismissed_before`: read file via `repos_get_content`, write Issue (title `[AEGIS] ...`, labels aegis+security, sections Summary/What is wrong/Impact/Fix with code/Evidence, last line `<!-- AEGIS-FP: <fingerprint> -->`), if `fix` present → create branch `aegis/fix-<fp>` from head sha, commit the patched file, open PR "AEGIS: fix <rule_id> in <path>" referencing the issue; if PR associated → request changes. After EVERY action call `aegis_scanner_record_action`.
- Output text: one-line summary + JSON `{verdict, issues_opened[], issues_closed[], prs_opened[]}`.
- `fleet/samples/push_clean.json`, `push_vuln.json`, `pull_request.json`: realistic GitHub payloads for repo `andriidrok1/aegis-demo-target` (use real shas from `git log` of `../aegis-demo-target` if present, else placeholders).
- Done when: `cd guild-agent/sentinel && npm install && guild agent test --mode json < ../../fleet/samples/push_vuln.json` runs and the session log shows `scan_diff` then GitHub calls (needs Andrii logged in + integration published; if not yet, make the code compile and document the command).

### A2 — fleet ops (owns `fleet/deploy.sh`, `fleet/policies.sh`, `fleet/context.md`, `skills/`)
- `fleet/deploy.sh`: reads `fleet.json`; for each agent: `guild agent init --name <agent> --template LLM --directory build/<agent>` (skip if `build/<agent>/guild.json` exists), copy `guild-agent/sentinel/*` over it, substitute `__AGENT_NAME__`, `npm install`, `guild agent save --message "deploy" --wait --publish`; then per repo create 2 webhook triggers (`push`, `pull_request`) with `--service-config '{"repo":"..."}'` and `--name <agent>-<repo>-<event>`; idempotent (check `guild trigger list` first); writes `fleet/triggers.json`. Also deploys `guild-agent/warden` with one CRON trigger.
- `fleet/policies.sh`: prints/applies per-agent credential policy JSON: ALLOW github operations on its 3 repos, DENY all other repos; include exact UI steps if CLI can't apply.
- `skills/security-review.md` (when a Semgrep finding is a true positive; severity rubric by CWE; attacker-controlled input → sink → consequence; Issue template) and `skills/remediation-playbook.md` (minimal patch rules; when to open PR vs Issue only; never change behaviour beyond the fix; how to write the PR body). Plus `skills/publish.sh` with the `guild skill` commands.
- `fleet/context.md`: workspace context (what AEGIS is, AEGIS-FP marker, fleet map, conventions) + list of workspace variables to set (`AGENT_NAME` is per agent → put it in guild.yaml/prompt instead; `FLEET_REPOS`, `SCANNER_URL`).
- Done when: scripts are executable, dry-run (`DRY=1`) prints every command; `guild trigger list` = 18 after a real run.

### A3 — specialists + warden + evals (owns `guild-agent/{triage,remediator,verifier,warden}/`, `evals/`)
- `triage`: input `{repo, sha, finding, context?}` → reads code → returns `{confirmed, confidence, severity, cwe, impact, explanation}` (output as JSON text).
- `remediator`: input `{repo, sha, finding, triage}` → Issue + branch + PR as described in A1 (A1 keeps a fallback inline; remediator is what sentinel delegates to once sub-agents work).
- `verifier`: input `{repo, sha, issue_number, fingerprint}` → comment + close.
- `warden`: cron input `{repos: [...]}` (or `{{env.FLEET_REPOS}}`) → for each repo `aegis_scanner_scan_full` → `aegis_scanner_fleet_insights(hours=24)` → writes a drift report Issue in repo `{{env.REPORT_REPO}}` (rising repos, noisy rules, reopened, per-agent p95) and sends email via `emailTools` if any ERROR-severity finding is new.
- Each has `guild.yaml`; sentinel's `guild.yaml` lists triage/remediator/verifier under `sub_agents`.
- `evals/sentinel.json`: samples = push_clean / push_vuln payloads; checks: `hasToolCall aegis_scanner_scan_diff`, `hasToolCall github_issues_create` (vuln), `hasToolCall github_repos_create_commit_status` (both), `llmJudge` "did not open an issue for a clean push".
- Done when: each agent compiles (`npm install` ok), `guild agent test` answers in the declared shape.

## After subagents finish (you, the session owner)
1. `git pull --rebase`, run `fleet/deploy.sh DRY=1`, then for real once Andrii is logged in and B has published the integration.
2. Full loop on target-01 with stream B+C: clean push → ✅, vuln push → ❌ + Issue + PR, merge PR → ✅ + Issue closed.
3. Commit after every working step: `git add -A && git commit -m "A: ..." && git pull --rebase && git push`.
