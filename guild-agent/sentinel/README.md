# AEGIS sentinel (Guild.ai agent)

One `llmAgent` per three GitHub repos. A GitHub webhook trigger (push / pull_request) hands it the raw payload; it
calls the `aegis-scanner` integration (`scan_diff`, or `scan_full` when `before` is all zeros), then acts on GitHub:

| verdict | commit status `AEGIS / security-check` | PR review | Issues |
|---|---|---|---|
| safe | success, "no new findings" | APPROVE | closes every open `aegis` Issue whose `<!-- AEGIS-FP: … -->` fingerprint is gone ("✅ Re-scanned … Closing.") |
| unsafe | failure, "N new finding(s)" | REQUEST_CHANGES | one Issue per new finding (skips open fingerprints and `dismissed_before`), fix PR on `aegis/fix-<fp>` when the finding carries a `fix` |

Every GitHub write is reported with `aegis_scanner_record_action`. Final output = one summary line + JSON
`{verdict, sha, issues_opened, issues_closed, prs_opened, dismissed}`.

Files: `agent.ts` (the agent), `guild.yaml` (fleet manifest read by `fleet/deploy.sh`, with a commented `sub_agents`
block for stream A3), `scannerToolsFallback.ts` (zod contracts of the 4 scanner tools, not imported by the agent),
`offline-types/` + `tsconfig.offline.json` (typecheck without registry access), `.npmrc` (Guild registry scopes).
Placeholders `__AGENT_NAME__` and `__OWNER__` are substituted by `fleet/deploy.sh`.

## Test

```bash
guild auth login                      # once; also writes the registry token into your npm config
cd guild-agent/sentinel
sed -i 's/__OWNER__/<guild-account>/g; s/__AGENT_NAME__/aegis-sentinel-01/g' agent.ts package.json guild.yaml   # or let fleet/deploy.sh do it
npm install
npx tsc --noEmit
guild agent test --mode json < ../../fleet/samples/push_vuln.json
```

What the session log should show for `push_vuln.json` (repo `vincivv/snipbox`, `168f7e8…` → `a3f1c2d…`):

1. `aegis_scanner_scan_diff {repo:"vincivv/snipbox", base_sha:"168f7e8e…", head_sha:"a3f1c2d4…", agent:"aegis-sentinel-01"}`
2. `github_issues_list_for_repo` (labels `aegis`), `github_repos_list_pull_requests_associated_with_commit`
3. `github_repos_create_commit_status` state `failure` → `aegis_scanner_record_action kind=status_set`
4. per finding: `github_repos_get_content` → `github_issues_create` → `record_action issue_opened` → (if `fix`)
   `github_git_create_ref` → `github_repos_create_or_update_file_contents` → `github_pulls_create` → `record_action pr_opened`
5. text output: `UNSAFE vincivv/snipbox@a3f1c2d: …` + the JSON line.

`push_clean.json` (`a3f1c2d…` → `b7e2d9c…`): `scan_diff` → `issues_list_for_repo` → `create_commit_status success` →
`issues_create_comment` + `issues_update closed` for each resolved fingerprint → `SAFE …`.
`pull_request.json` (synchronize, PR #7): same as a push but base/head come from `pull_request.base.sha` /
`pull_request.head.sha`, and the review lands on PR #7.

Requirements for a real run: Andrii logged in, the GitHub credential installed on the target repos, stream B's
`aegis-scanner` integration published and connected (API key `X-AEGIS-Key`).

## Compile check (state on 2026-10-09)

- The SDK and integrations live on Guild's private registry `https://app.guild.ai/npm/` (scopes `@guildai`,
  `@guildai-services`, `@guildai-agents`; the CLI writes the token into the user npm config on `guild auth login`).
  Unauthenticated requests are rejected (`npm view` → 401), and a real `npm install` here aborted on
  `@guildai-services/__OWNER__~aegis-scanner` → 404 (placeholder owner, package not published yet), so install and version discovery
  (`npm view @guildai/agents-sdk version`) could not run here. Versions are left as `*` (the CLI docs use `*` for
  integrations) and zod is pinned `~4.3.0` (4.3.4–4.3.6 on npm).
- `npx tsc -p tsconfig.offline.json --noEmit` → **exit 0** (typescript 5 + zod 4.3 from a scratch dir, offline shims).
- `npx tsc --noEmit` → fails only with `TS2307 Cannot find module` on the three Guild imports (expected until install).
- The `@guildai-services/__OWNER__~aegis-scanner` import is a normal import, not behind `@ts-ignore`: with the offline
  shim it type-checks, and after stream B publishes it resolves for real. If it is the only unresolved module after a
  real `npm install`, comment that import plus the `pick(AegisScannerTools, …)` spread to test the GitHub half alone.

## Tool names: verified vs derived

GitHub tool names are `github_` + the GitHub REST `operationId` in snake_case. Names that appear verbatim in the
Guild CLI's bundled docs (`@guildai/cli/docs/skills/*.md`): `github_issues_list_for_repo`, `github_issues_create`,
`github_issues_create_comment`, `github_pulls_create`, `github_git_get_ref`, `github_git_create_ref`.
Derived from the same convention, not yet seen in installed types: `github_repos_get_content`,
`github_repos_create_commit_status`, `github_repos_list_pull_requests_associated_with_commit`, `github_issues_update`,
`github_pulls_create_review`, `github_repos_create_or_update_file_contents`. No name was substituted. Verify after
install with

```bash
guild integration operation list guildai~github | grep -E 'get_content|create_commit_status|associated_with_commit|issues_update|create_review|create_or_update_file'
grep -ho 'github_[a-z_]*' node_modules/@guildai-services/guildai~github/dist/*.d.ts | sort -u
```

Scanner tools: `aegis_scanner_scan_diff`, `aegis_scanner_scan_full`, `aegis_scanner_record_action`
(`aegis_scanner_fleet_insights` is for the warden). The export is **`AegisScannerTools`** (PascalCase): the CLI docs
derive custom-integration exports as `aegis-scanner → aegis_scanner → AegisScanner + Tools`; only first-party
`guildai~*` packages use camelCase like `gitHubTools`. The stream brief said `aegisScannerTools`; if the published
package turns out camelCase, change the one import line.

SDK built-ins used: `skillsTools` (documented export, gives `skills_search` / `skills_activate`). Custom-tool API: the
SDK has `tool({execute})` and `guildServiceTool()`, neither usable for a local scanner stub (no network in the sandbox;
`guildServiceTool` is for authoring integration packages, not agents). Details in `scannerToolsFallback.ts`.

## Known weak spot

The fix PR asks the model to base64-decode the file, splice `fix` over `lines`, and re-encode. Reliable for small files
(snipbox files are < 100 lines; the prompt refuses above 200 lines). The long-term home for this is the A3 remediator
sub-agent implemented as a coded `"use agent"` with `Buffer.from(...)`.
