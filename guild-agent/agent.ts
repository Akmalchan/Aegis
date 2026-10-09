// AEGIS analyst agent — hosted on Guild.ai.
// Receives one Semgrep finding (from the scanner via an API trigger), investigates it using Guild's GitHub
// integration, and takes the autonomous action: opens a GitHub Issue (mode=investigate) or verifies + closes it (mode=verify).
import { llmAgent, pick } from "@guildai/agents-sdk"
import { gitHubTools } from "@guildai-services/guildai~github"
import { z } from "zod"

const Finding = z.object({
  rule_id: z.string(),
  path: z.string(),
  start_line: z.number().nullable(),
  end_line: z.number().nullable(),
  lines: z.string(),
  message: z.string(),
  severity: z.string(),
  cwe: z.string().optional(),
  fingerprint: z.string(),
  context: z.string().optional(),
})

export default llmAgent({
  inputSchema: z.object({
    mode: z.enum(["investigate", "verify"]),
    agent_name: z.string(),
    owner: z.string(),
    repo: z.string(),
    commit: z.string(),
    finding: Finding,
    issue_number: z.number().optional(),
  }),
  outputSchema: z.object({
    action: z.enum(["issue_opened", "issue_closed", "dismissed"]),
    issue_number: z.number().optional(),
    severity: z.string().optional(),
    confidence: z.number().optional(),
    summary: z.string(),
  }),
  tools: {
    ...pick(gitHubTools, [
      "github_repos_get_content",
      "github_issues_create",
      "github_issues_create_comment",
      "github_issues_update",
      "github_issues_list_for_repo",
    ]),
  },
  mode: "single-turn",
  systemPrompt: `You are AEGIS, an autonomous application-security analyst responsible for a small set of repositories.
You are given ONE Semgrep finding for {owner}/{repo} at commit {commit}.

MODE = investigate:
1. Read the affected file with github_repos_get_content (ref = the commit) to understand the surrounding code. Use the provided
   "context" snippet if the tool fails.
2. Decide if this is a real, reachable weakness. Identify the attacker-controlled input, the dangerous sink, and the concrete consequence.
3. If confirmed with confidence >= 0.6, create a GitHub issue with github_issues_create:
   - title: "[AEGIS] <short precise title>"
   - labels: ["aegis", "security"]
   - body in Markdown with sections: Summary (severity, confidence, rule id, CWE, file:lines @ short sha), What is wrong,
     Impact, Recommended fix (with a corrected code snippet), Evidence (the Semgrep lines + message).
   - The body MUST end with these two lines exactly:
     _Filed autonomously by AEGIS agent **<agent_name>** on Guild.ai. Push a fix and I will re-scan and close this issue._
     <!-- AEGIS-FP: <fingerprint> -->
   Return action=issue_opened with the issue number.
4. If not confirmed, do NOT create an issue; return action=dismissed with the reason.

MODE = verify:
The scanner re-scanned the file at the new commit and the finding is gone. Add a comment on issue #{issue_number} with
github_issues_create_comment ("✅ Re-scanned <path> at <short sha>: <rule_id> no longer present. Closing. — AEGIS agent <agent_name>")
then close it with github_issues_update (state: "closed", state_reason: "completed"). Return action=issue_closed.

Be concrete and brief. Never invent findings. Never modify code yourself; you report and track.`,
})
