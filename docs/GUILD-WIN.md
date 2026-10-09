# Winning "Best use of Guild to host and run agents"

Checked 2026-10-09 ~15:05 against guild CLI 0.27.1, `guild trigger/agent/credentials` output and docs.guild.ai. Guild's own pitch (launch, 29 Apr 2026): "control plane for AI agents" with scoped credentials, policies, approval gates, audit trails, cost controls. Pitch to that.

## A. What we already use (honest)

- 10 TypeScript `llmAgent`s, all owned by andriidrok1, in workspace `andriidrok1~aegis`.
- Sub-agents as tools: sentinel-01 v1.0.5 imports `@guildai/andriidrok1~aegis-{triage,remediator,verifier}/tool` from the Guild npm registry. Sentinels 02/03 still run the inline fallback.
- 23 triggers: 21 GitHub webhooks (push, pull_request, issues) and 2 cron (warden, reporter). One onboarder trigger on `Akmalchan/Aegis` is disabled.
- Custom integration `aegis-scanner` from OpenAPI; the proxy injects `X-AEGIS-Key`.
- Credential policies ARE applied on the `guildai~github` credential (9 rules: per-sentinel ALLOW on own repos, DENY on the others, warden ALLOW). A deny was proven in session `01a12270-29cd-…`.
- LLM policy: workspace `aegis` pinned to OpenAI `gpt-4o`.
- Two skills; eval spec `evals/sentinel.json`, never run.
- Not used: Evals UI, Optimizations, runtime environments, Goose/OpenClaw, MCP server, Slack, Agent Hub (agents are Internal), `ui.prompt`, workspace member restriction, audit logs.

Two gaps a DevRel head may spot: policy `01a1225e-d131-…` is ALLOW all/all/all, so triage, remediator, verifier, rulesmith and onboarder (the ones that actually write) are unfenced; and `set_status` bypasses the GitHub policy via our scanner.

## B. Six additions, ranked by judge impact × doable in 30 min

1. **Run the eval and show the score.** Spec exists. app.guild.ai → Agents → aegis-sentinel-01 → Setup → Evals → paste `evals/sentinel.json`, put real shas from `git -C demo-target rev-parse`. Results come on three axes (structural, llmJudge, cost) and are never blended. Demo moment: "every new sentinel version is scored before the fleet gets it". Risk: evals may be gated per account; ask at the Guild booth.

2. **Semgrep inside Guild (Goose + runtime environment).** Goose is the only agent type that takes `environment:`; setup scripts run with network, then the container goes offline and only Guild proxies remain. So install at setup time:
   ```bash
   printf '#!/usr/bin/env bash\nset -e\npip install semgrep\ngit clone --depth 50 https://github.com/andriidrok1/aegis-demo-target /work/target\n' > /tmp/setup.sh
   guild runtime-environment create --owner andriidrok1 --name aegis-semgrep --image guildai~goosebox --setup @/tmp/setup.sh
   guild runtime-environment test --owner andriidrok1 --name aegis-semgrep
   guild agent init --name aegis-semgrep-runner --agent-type goose   # guild.yaml: environment: andriidrok1~aegis-semgrep
   ```
   Unverified: whether Goose on Guild has a shell (recipe `extensions` are rejected, so the developer extension may be absent). OpenClaw has a shell for sure, but is pinned to `guildai~lobsterpot` and offline, so no semgrep. Timebox the spike to 20 min; if `semgrep --version` does not run inside a session, drop it. Demo moment: the scan runs in Guild's isolated container, with no tunnel.

3. **Close the policy gap, then show a live deny.** Add ALLOW rules for triage/remediator/verifier limited to the fleet repos, then `guild credentials policy delete 01a1225e-d131-02e7-0000-c3da7083897e`. Re-run `fleet/samples/push_foreign.json` and show Security events `decision: deny`. Rehearse once: deleting the catch-all can break a path we forgot.

4. **Guild MCP server driving the fleet.** `guild setup --mcp` (or `.mcp.json` with `guild mcp`). In Claude Code: "list the sessions trigger X spawned in the last hour" via `guild_get_trigger_sessions`. 5 min, mid impact.

5. **Slack alert from reporter/sentinel.** `guildServiceTool("slack", …)` `slack_chat_post_message` per docs; needs a Slack credential connected. ~20 min, visible but generic.

6. **Agent Hub.** Making an agent Public is permanent, and a public agent cannot depend on a private integration or sub-agent, so only triage could go (check its deps). Low value for the risk.

Skipped: `task.ui.prompt` before merge (stalls the autonomous loop), Factory, Optimizations.

## C. Do now

1. Eval run in the UI with a visible score (B1).
2. Policy gap fix plus live deny on screen (B3). Run the Goose/semgrep spike (B2) in parallel only if a second person is free; it is the biggest win and the biggest risk.

## D. Talking points for Corbett

- "The agents never see a secret": GitHub and scanner keys are injected by Guild's proxy, and a sentinel touching a foreign repo is refused before GitHub (`http_status_code: null`).
- "Agents compose like packages": sentinel imports triage/remediator/verifier from the Guild npm registry, typed by their input schemas.
- "Scaling is config": 100 sentinels is `fleet.json` plus `fleet/deploy.sh`, which creates agents, webhook triggers and policies from one file.
- What we learned about Guild and would ask for: commit-status operation in the GitHub integration, a shell-capable runtime for scanners, and evals wired into `guild agent save`.

Sources: docs.guild.ai, [Guild launch PR](https://pr.tiftongazette.com/article/Guildai-Introduces-the-First-Control-Plane-for-AI-Agents/69f23d6d25e04a842cd6ac40).
