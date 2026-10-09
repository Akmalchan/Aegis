# Submission form: AEGIS

Paste each block into the matching field at tokensand.com/cyberhack/submit.

## Project name

AEGIS

## One-liner

A fleet of autonomous security agents on Guild.ai. On every push they scan the diff with Semgrep, then file the Issue and open the fix PR themselves, and ClickHouse makes the fleet learn from every decision.

## Description (≤ 300 words)

<!-- word count of the text below: 269 (wc -w) -->

AI agents now write code faster than any security team can review it. AEGIS is a security team that scales the same way: a fleet of autonomous agents, each owning three GitHub repositories, that never sleeps.

Every push or pull request fires a Guild.ai webhook trigger that wakes the repo's sentinel agent. The sentinel calls our scanner through a Guild custom integration. The scanner runs Semgrep with --baseline-commit, so it answers exactly one question: did this change make the repo unsafe? If it did, the agent sets a red commit status, triages the finding by reading the code, files a GitHub Issue with impact and fix, and opens a pull request with the patch from the rule's autofix. If the change is safe, the commit goes green, the PR is approved, and any Issue whose finding disappeared is closed with "re-scanned at <sha>". Nobody clicks anything.

Governance is enforced by infrastructure, not prompts. Guild credential policies let each sentinel write only to its own repos, and any attempt on a foreign repo is denied and logged.

ClickHouse is the fleet's memory. Every scan, finding and action is stored, and that history changes behaviour. A false positive dismissed once is suppressed everywhere, findings are prioritised by each repo's mean time to remediate, and a warden agent on a cron trigger writes drift reports: rising repos, noisy rules, reopened findings and agent latency. We backfilled Semgrep over the full git history of the fleet, tens of thousands of findings, into a live posture timeline.

Three agents and ten repos today. A hundred agents and three hundred repos is one config file.

## Tools used

Guild.ai (agent hosting, webhook and cron triggers, custom integration, credential policies, skills, sub-agents, evals, LLM provider) · Semgrep (diff scanning with --baseline-commit, custom rules with autofix, p/security-audit, p/secrets, optional Supply Chain) · ClickHouse (fleet memory, enrichment, insights, history backfill, live dashboard) · OpenAI (through Guild's LLM provider) · GitHub API · FastAPI · cloudflared

## Sponsor prizes to tick

- [x] **Pi (overall)**: a complete autonomous loop (detect, explain, patch, verify, close) running live on real GitHub repos, with no human in the loop except `git push`.
- [x] **Guild.ai**: every agent is hosted on Guild, woken by Guild webhook and cron triggers, and calls our scanner as a Guild custom integration. Each agent is fenced by Guild credential policies, and the deny events are part of the demo.
- [x] **Semgrep**: Semgrep is the detection engine. It runs `--baseline-commit` diff scans, our own rules carry `fix:` autofixes that become the agent's PR, and the registry packs and secrets rules run on every scan, on real vulnerabilities found in the target repos.
- [x] **ClickHouse**: real-time analytics that directly drive detection and remediation (fleet-wide false-positive suppression, MTTR prioritisation, drift insights for the warden) over a full git-history backfill of `<N>` findings, queried live by the dashboard in milliseconds.

## Repository

https://github.com/Akmalchan/Aegis

## Demo video

`<VIDEO URL>`

## Team

| Name | Email |
|---|---|
| Andrii | `<email>` |
| Akmal | `<email>` |
| `<person 3>` | `<email>` |
