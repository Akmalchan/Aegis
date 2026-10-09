// AEGIS verifier — sub-agent of aegis-sentinel-NN. Hosted on Guild.ai.
// Input: an AEGIS issue whose fingerprint disappeared from the latest scan. Comments and closes it.
import { llmAgent, pick } from "@guildai/agents-sdk"
import { gitHubTools } from "@guildai-services/guildai~github"
// @ts-ignore TODO: package appears once stream B publishes the custom integration (`guild integration ...`).
import { aegisScannerTools } from "@guildai-services/__OWNER__~aegis-scanner"
import { z } from "zod"

export default llmAgent({
  inputSchema: z.object({
    repo: z.string().describe("owner/name"),
    sha: z.string().describe("commit at which the re-scan no longer reports the finding"),
    agent: z.string().describe("calling sentinel, e.g. aegis-sentinel-01"),
    issue_number: z.number(),
    fingerprint: z.string(),
    path: z.string(),
    rule_id: z.string(),
  }),
  tools: {
    ...pick(gitHubTools, ["github_issues_create_comment", "github_issues_update"]),
    ...pick(aegisScannerTools, ["aegis_scanner_record_action"]),
  },
  mode: "one-shot",
  llmPreferences: [{ provider: "openai" }, { provider: "anthropic" }],
  inputTemplate:
    "Close issue #{{issue_number}} in {{repo}}: {{rule_id}} in {{path}} is gone at {{sha}} (fingerprint {{fingerprint}}, agent {{agent}}).",
  systemPrompt: `You are AEGIS verifier. The scanner re-scanned the repo and the finding behind one AEGIS issue is gone.
Split "repo" into owner and repo. short sha = first 7 chars of sha. Do exactly three calls, in order:

1. github_issues_create_comment {owner, repo, issue_number, body:
   "✅ Re-scanned <path> at <short sha>: <rule_id> no longer present. Closing. — AEGIS agent <agent>"}
2. github_issues_update {owner, repo, issue_number, state: "closed", state_reason: "completed"}
3. aegis_scanner_record_action {agent, repo, kind: "issue_closed", ref: "<issue_number>", fingerprint}

Never reopen, relabel or edit the issue body. If a call fails, retry once, then report it.
OUTPUT: only a JSON object: {"closed": true|false, "issue_number": <int>, "notes": "<short>"}`,
})
