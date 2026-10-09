# How sentinel delegates to the specialists

Sentinel keeps the loop (scan, commit status, bookkeeping); the three specialists do the slow, judgement-heavy parts.
Call order per push after `aegis_scanner_scan_diff` returns `verdict: unsafe`:

1. For each finding not already open (no `<!-- AEGIS-FP: fp -->` in an open issue) and not `dismissed_before`:
   `aegis_triage({repo, sha: after, agent, finding})` -> JSON `{confirmed, confidence, severity, ...}`.
2. If `confirmed && confidence >= 0.6`: `aegis_remediator({repo, sha: after, agent, finding, triage})` -> `{issue_number, pr_number}`.
   Else: `aegis_scanner_record_action(kind: "dismissed", fingerprint)` and no issue.
3. On `verdict: safe`, for each open AEGIS issue whose fingerprint is absent from `findings`:
   `aegis_verifier({repo, sha: after, agent, issue_number, fingerprint, path, rule_id})` -> `{closed: true}`.

Tool names: sub-agents declared in `guild.yaml` surface as tools of toolType "agent". The docs do not state the
generated name; we assume the agent name in snake case (`aegis_triage`, `aegis_remediator`, `aegis_verifier`).
Verify with `guild agent capabilities <owner>~aegis-sentinel-01` after publish and fix the names in the sentinel prompt.
Caveat from docs.guild.ai/guide/guild-yaml: `sub_agents` is listed for Goose, Native, OpenClaw and LangGraph agents;
TypeScript agents are not named there, so delegation may not resolve at all for sentinel.

Fallback (40-minute rule from docs/streams/A.md): if the agent tools do not show up or calls fail, sentinel runs the
same three procedures inline (its prompt already contains the Issue template, PR steps and close comment); the
specialists stay published for the warden/demo and for the eval of each piece in isolation.
