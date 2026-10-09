# Sub-agents for TypeScript Guild agents (verified 2026-10-09)

## Outcome 13:14 — WIRED (W)

`@guildai/andriidrok1~aegis-{triage,remediator,verifier}` resolved from the Guild registry (`.npmrc`
`@guildai:registry=https://app.guild.ai/npm/`; `npm view` from a dir without that .npmrc 404s, which is a red herring).
`build/aegis-sentinel-01`: `npm install --save` of the three `^1.0.4` packages, `agent.ts` imports `.../tool` and
exposes them as `aegis_triage` / `aegis_remediator` / `aegis_verifier`; local `tsc` clean; `guild agent save --wait
--publish` PASSED → `andriidrok1~aegis-sentinel-01` v1.0.5 (`01a1224c-7922-cf83-0000-26bd6835e4dc`). The server
metadata lists the three as `toolType: "agent"` next to `aegis_scanner_set_status`. Inline procedure kept in the
prompt under `INLINE FALLBACK`. `fleet/deploy.sh` installs the sub-agent packages for the sentinel template only.
Order on a fresh deploy: publish triage/remediator/verifier first, then the sentinels. Not yet exercised on a real
push after the wiring; `guild agent test` with `fleet/samples/push_vuln.json` is the next check.

Sources checked: `@guildai/agents-sdk` 0.7.8 d.ts (`dist/llm-agent.d.ts`, `dist/services/utils.d.ts`),
`@guildai/cli` 0.27.1 docs (`docs/skills/agent-dev.md` "Calling Another Agent",
`docs/skills/agent-dev-references/integrations.md` "Agent-to-agent delegation"), docs.guild.ai/guide/guild-yaml,
and the published agent package `@guildai/guildai~sys-experimental-coding` (exports `.` and `./tool`).

## Verdict: supported, wired in code, not in guild.yaml

- `guild.yaml` (`sub_agents:`) is for Goose / Native / OpenClaw / LangGraph agents only. The reference says it
  outright: "TypeScript agents don't use guild.yaml. They declare tools in code." The file
  `sentinel/guild.yaml.sub_agents.snippet` is therefore wrong for our agent type and must not be pasted.
- There is no `subAgents` / `agents` option on `llmAgent`. The 0.7.8 `Params` type has: `description`, `tools`,
  `systemPrompt`, `mode`, `multiTurnStopBehavior`, `toolCallResponseStream`, `llmPreferences`,
  `useWorkspaceAgents`, `inputSchema`, `inputTemplate` (`identifier` is deprecated).
- A sub-agent is just a tool. Every **published** agent gets an npm package `@guildai/<owner>~<name>` on the Guild
  registry with a `./tool` sub-package (`exports: {".": "./dist/agent.js", "./tool": "./dist/tooldef.js"}`),
  auto-generated at build time with `guildAgentTool({description, inputSchema, outputSchema, calls})`. It inherits the
  sub-agent's `inputSchema`, so the caller's tool call is typed with our `{repo, sha, agent, finding}` objects.
  The output of an `llmAgent` is always `{type: "text", text: string}`; our sub-agents put JSON in `text`.
- The caller adds the package as a dependency and spreads the default export into `tools`. The tool name the LLM
  sees is the key you choose:

```ts
import triageTool from "@guildai/__OWNER__~aegis-triage/tool"
import remediatorTool from "@guildai/__OWNER__~aegis-remediator/tool"
import verifierTool from "@guildai/__OWNER__~aegis-verifier/tool"

const tools = { ...existingTools, aegis_triage: triageTool, aegis_remediator: remediatorTool, aegis_verifier: verifierTool }
```

- Dynamic alternative: `useWorkspaceAgents: true` makes every agent installed in the workspace callable without a
  dependency. Non-deterministic (names, versions), so the explicit `/tool` import is the right way for the sentinel.
- Order of operations: the sub-agents must be published (`guild agent save --message ... --wait --publish`) before
  the sentinel's `npm install` can resolve `@guildai/<owner>~aegis-triage`. A draft version is not enough for the
  registry (checked after `guild agent save --wait` without `--publish`: see the R3 report; if `npm view` shows the
  package only after publish, publish first). `fleet/deploy.sh` must then run
  `npm install --save @guildai/$OWNER~aegis-triage @guildai/$OWNER~aegis-remediator @guildai/$OWNER~aegis-verifier`
  in the sentinel build dir (it currently installs only the SDK and the scanner integration; package.json is
  excluded from the rsync, so the dependency has to be added there).
- A public agent cannot depend on a private sub-agent (docs.guild.ai/guide/guild-yaml "Access and permissions"). Keep
  the sentinel and the three specialists at the same visibility.

The exact edits for `sentinel/agent.ts` are in `SUBAGENTS.patch.md` (R1 owns that file).

## Call protocol (what the sentinel prompt must say)

After `aegis_scanner_scan_diff` returns `verdict: unsafe`:

1. For each finding not already open (no `<!-- AEGIS-FP: fp -->` in an open `aegis` issue) and not
   `dismissed_before`: `aegis_triage({repo, sha: HEAD, agent: NAME, finding})`. The result is
   `{type: "text", text: "<json>"}`; parse `text` as `{confirmed, confidence, severity, cwe, title, impact,
   explanation, fix_suggestion}`.
2. If `confirmed && confidence >= 0.6`: `aegis_remediator({repo, sha: HEAD, agent: NAME, branch: BRANCH, finding, triage})`
   → text JSON `{issue_number, pr_number, fix_sha, verified, layers, notes}`. `branch` (optional, since 1.0.5) is the
   pushed branch and becomes the PR base; without it the remediator uses the repo default branch. The remediator
   records `issue_opened` / `pr_opened` / `verified` / `verify_failed` itself.
   Else: `aegis_scanner_record_action({agent: NAME, repo, kind: "dismissed", ref: HEAD, fingerprint})` and no issue.
3. On `verdict: safe`, for each open AEGIS issue whose fingerprint is absent from `findings`:
   `aegis_verifier({repo, sha: HEAD, agent: NAME, issue_number, fingerprint, path, rule_id})` → text JSON
   `{closed, issue_number, notes}`. The verifier records `issue_closed` itself.

Pass the finding object exactly as the scanner returned it; the sub-agent input schemas reject missing required
fields (`rule_id, path, start_line, end_line, lines, message, severity, fingerprint`). Scanner 1.1.0 also returns
`fix_hint`; `Finding` in triage/remediator declares it since 1.0.5 (R4). Before that the sentinel LLM silently dropped
the key to satisfy the tool schema (seen in session `01a12251-10bd-…`, 13:18): no validation error, but the hint was lost.

## Real run 13:18 (R4, `fleet/samples/push_vuln_real.json`, GitHub credential NOT connected)

`scan_diff` → `unsafe`, 4 findings (hardcoded-secret ERROR + fix, sql-string-concat ERROR + fix_hint, flask-debug-true
WARNING + fix, avoid_app_run_with_bad_host WARNING) → `issues_list_for_repo` + `list_pull_requests_associated_with_commit`
(both Unauthorized) → `skills_search` + 2× `skills_activate` → 4× `aegis_triage` in parallel (each tried
`repos_get_content`, Unauthorized, judged from `lines`, all `confirmed: true, confidence: 0.8`) → `aegis_remediator`
for the secret only (`issues_create` Unauthorized, returned `issue_number: null` with prose before the JSON) →
`set_status failure "4 new findings"` (ok: true, real status set by the scanner) → `create_commit_comment` (Unauthorized)
→ final JSON `verdict: unsafe`. No schema error on any sub-agent call. Two prompt bugs seen and fixed in 1.0.6 / 1.0.5:
the status was set last instead of first, and the sentinel stopped delegating after the first GitHub failure.

## Fallback

If the sub-agent packages cannot be installed in time (not published, visibility mismatch, registry resolution
error in the server build), the sentinel keeps doing triage / remediation / verification inline. Its prompt already
contains the Issue template, the PR steps and the close comment, so nothing is lost except the per-piece evals and
the parallel fan-out. The specialists stay published for the warden demo and for isolated evals.

## Things learned on the way (affect R1 too)

- Local `npm install` with npm < 11.7 resolves `@guildai/agents-sdk@*` to **0.1.0** (every newer version declares
  `engines.npm >= 11.7`), and 0.1.0 has no `inputSchema` / `inputTemplate` / `useWorkspaceAgents`. The server build
  (npm 11.12) gets 0.7.8. Install `@guildai/agents-sdk@latest` locally so `tsc` checks the real API; the lock file then
  pins 0.7.8 for the server as well. `fleet/deploy.sh` already installs `^0.7.8`.
- The `inputTemplate` renderer is NOT Mustache. `dist/llm-agent.js` `render()` is a plain
  `{{dotted.path}}` replacer: strings verbatim, numbers as text, anything else via `JSON.stringify`, no escaping,
  no sections. So `{{finding}}` renders the whole object as JSON (good), but `{{#commits}}...{{/commits}}` and
  triple-brace `{{{x}}}` are not understood: `{{#commits}}` becomes an empty string plus the literal body text, and
  `{{{head_commit.message}}}` leaves a stray `}` and renders empty. The sentinel template currently uses both.
- `llmPreferences` are strict in 0.7.8: "if none can be used under the account's configuration, LLM calls fail rather
  than falling back to the session default". The four specialists omit it and run on the session default.
- `@guildai-services/guildai~email` does not exist on the registry (404; also no mail/sendgrid/resend/smtp/gmail).
  `@guildai-services/guildai~slack` 2.1.1 exists if a chat alert is wanted later.
- `aegis_scanner_fleet_insights` takes no parameters (`z.object({})`); the response arrays are optional and untyped.
